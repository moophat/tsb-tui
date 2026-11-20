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

## Fix round after the review

**AI-Model:** glm-5.3-flash (opencode-go) | **Branch:** feat/tui-redesign-p2b, rebased on origin/main 7d601e8, then one fix commit | **Date:** 2026-11-20

### The rebase

`git rebase origin/main` (7d601e8, #103) on 3f88d90 — **no conflicts**; git's three-way merge kept both sides:
`tsb_tui/model/attachment.py` came out byte-identical to 7d601e8's (`diff` empty), and `tsb_tui/screens/terminal.py`
carries #103's release path whole (`_free_canvas`, `_freeing`, the pump's `shutdown_feed` / `feed_running` /
`feed_stopped` / `join_feed` / `_run_sink` untouched) beside P2b's changes. The commit kept its message and trailers
(git rebase default). Verified after the rebase: `tests/test_drive_login_status.py tests/test_sign_out.py
tests/test_attach_and_close.py` all pass (41 tests).

### Rulings and their changes

0. Rebase — above.
1. **Menu + Ctrl+]** (`app.py` `handle_menu_key`): Ctrl+] from a Menu opened directly (F1) detaches the front
   session (via `run_action_id("detach")`, the Menu closes); only a pending leader followed by Ctrl+] sends the
   byte. The check sits before the search-mode return, so it holds in the directly-opened (searching) Menu too.
   The leader-twice path is unchanged; the existing leader-then-Ctrl+] test stays green.
2. **Rail rows** (`terminal.py` `_sync_fleet_widgets`): the rail's chip ids are filtered to the sessions open in
   this window first, then capped and numbered (`fleet.chips` over a scan copy with the filtered `chip_ids`);
   `more` is `len(rail_ids) > MAX_CHIPS`. The side panel's and full view's numbering and the leader digits keep
   the model's own numbering (unchanged, as flagged in the r2 concerns).
3. **Keyboard notice** (`terminal.py` `_tell_keyboard_moved`): when the same slot loses the keyboard, the frame's
   "you type now" notice is cleared at once (`keyboard_notice = ""`, `keyboard_notice_until = 0.0`, status
   refresh); the leaving toast stays.
4. **Keys row while the filter is edited** (declined: no behaviour change where the keyboard lands — but the row
   did advertise keys that do not work there: `⏎ New session here`, `n New session`, `/ Filter`, `Esc Back`,
   `z Fold`, and wide `F Full view`). Now (`terminal.py` `_sidebar_keys_target`) it shows the editing keys only:
   `⏎ Done` (Enter ends editing), `Esc Clear` (Esc empties the filter). `Esc Back` is never advertised there now,
   which is what the sidebar does while editing.
5. **All keys text** (`widgets/sidebar.py`, the Targets filter line's tip): "/ filter the targets by name or host
   (an IP included)". No matching changed.
6. **Custom themes** (`widgets/sidebar.py` `_build_styles`): if any foreground role the panel draws (the style
   table's nine: text, text_muted, text_faint, accent, steel, warn, danger, rec_list, border_on_surface) falls
   below 4.5:1 on the Sessions tint, the Sessions section keeps the Targets surface (no second step). Shipped
   themes pass 4.5:1 on both tints, so they keep the tint; the five-theme tint test is untouched and green.
7. **Enter on the rail's Targets or Sessions rows** (`terminal.py` `on_rail_activated`): both open the panel with
   the fleet read (`open_sidebar` default `refresh=True`, the same read a click and `^\ f` do); Targets opens its
   list, Sessions its list.
8. **Leader map** (declined: no behaviour change): verified the refusal — a custom `[keys.leader_map]` naming a
   moved default letter is refused at start-up by `leader_keys` → `KeymapError` → `ConfigError`, message naming
   the key (e.g. a map putting `event_log` on `n`: "…'new_session' and 'event_log' are both on 'n' after the
   leader"). One line added to the README's keys paragraph and the user guide's keys section: the letters that
   moved in this release (n, →, ←, D, y, h) and that a custom leader map naming them must be updated.
9. **Ended session screen**: `n` is New session (`c` stays an unadvertised alias), next and previous open session
   are `→` and `←`; the ended row's hints say so (`n New session here`, `→ Next open session`, `← Previous open
   session`, no `c`). Both layers changed: the `EndedScreen` dialog's bindings (n, c, →, ←; a
   `previous_session` action added) and the workspace's ended branch in `on_key`.
10. **The comment above the action table** (`model/actions.py`) names the current letters: New session `n`
    (`c` an unadvertised alias), Next `→`, Previous `←`.

### Tests first, red on the rebased code (all in `tests/test_redesign_rail_tint_keys.py`, 14 new, keys only)

Each red before its fix; the red run is `.lane-logs/red-first-run.txt` (14 failed).

| Test | Red line on the rebased code |
|---|---|
| menu opened directly, Ctrl+] detaches (x2 sizes) | `timed out after 10s waiting for the front session to detach` |
| rail numbers the open sessions first (x2) | `ValueError: too many values to unpack` on the first run (test bug, fixed), then the chips assertion: today no rail chip exists for the open sessions |
| frame notice clears when the keyboard leaves (x2) | `timed out after 2s waiting for the notice to clear at once` (the notice lived on) |
| keys row while the filter is edited (x2) | `[('⏎', 'New session here'), ('n', 'New session'), ('/', 'Filter'), ('Esc', 'Back'), ('z', 'Fold')]` (+ `('F', 'Full view')` wide) |
| custom theme the tint cannot carry (x2) | `assert {'#3f4657'} == {'#39404f'}` — Sessions painted the tint |
| Enter on the rail rows reads the fleet (x2) | `timed out after 10s waiting for the fleet read on Enter` |
| ended screen answers n and the arrows (x2) | `[('c', 'New session here'), ('n', 'Next open session'), ('Ctrl+\\ f', 'Targets')]` |

Green: the same file, 56 passed (42 + 14). Two existing assertions pinned the pre-ruling behaviour and moved with
the rulings: `test_sidebar.py` `test_the_panels_old_entries_are_the_rails_and_the_keys_now` (the editing row's
hints) and `test_ended_and_reattach.py` `test_esc_closes_the_dialog…` (renamed; the row says `n New session here`
and the `c` press stays, proving the alias). Baselines regenerated: only `redesign-p2b/first-focus-80x24` and
`-160x45` (the keys row while the filter is edited); the diff is that row alone.

### Commands (NO_COLOR unset — `env -u NO_COLOR` on the rerun — `PYTHONPATH=program/contract/client:.` — test venv python)

- New tests red: 14 failed (exit 1, recorded). Green: `tests/test_redesign_rail_tint_keys.py` 56 passed (exit 0;
  three runs: plain, `NO_COLOR=` empty, `env -u NO_COLOR`).
- `tests/test_drive_login_status.py tests/test_sign_out.py tests/test_attach_and_close.py`: 41 passed (exit 0).
- `tests/test_redesign_focus.py tests/test_redesign_rail_tint_keys.py tests/test_rail.py tests/test_sidebar.py
  tests/test_status_row.py tests/test_leader_and_menu.py tests/test_actions.py tests/test_ended_and_reattach.py`:
  241 passed (exit 0; the first pass had the four pre-ruling failures above, fixed, then green).
- `make contract-check`: exit 0 (program clean at c4c2b81bb7a1, v0.18.0). `make lint`: exit 0 (after two unused
  local names in the new tests were dropped).
- The leader-map refusal probed live: `leader_keys({"event_log": "n"})`, `({"event_log": "y"})`,
  `({"next": "n"})` each raise the message naming the key.

### Not verified

- No real terminal or PTY run; everything through the in-process fixture server.
- `tests/test_snapshots.py` and the full suite were not run (outside this round's gate); the only baseline change
  is the two `first-focus` SVGs above.
- The rail's renumbering bites only when a `rail.chips` knob puts `mine_held_by_others` ahead of `open_here`
  (the default order lists open-here first, so under defaults nothing moves); the test pins it through the knob.

## Fix round 2 (P2b review round 2)

**AI-Model:** swift-1.5 (Strata) | **Branch:** feat/tui-redesign-p2b (483b68b, 4aa65da on main 7d601e8) | **Date:** 2026-11-20
**Status:** DONE — item 2 done; item 1 reverted to the build's behaviour by the orchestrator's ruling (2026-11-20 09:1x). One commit: the two baselines and this report. No push.

### Item 2 — the two baselines (done)

Round 1 changed the keys row while the Targets filter is edited (`⏎ Done`, `Esc Clear`), so
`tests/snapshots/redesign-p2a/panel-focused-80x24.svg` and `-160x45.svg` no longer matched. Regenerated only those
two, the way the test file's own path does: `UPDATE_P2A_SNAPSHOTS=1` on
`test_redesign_layout.py::test_two_size_baselines` for the `panel-focused` params (the `_snapshot` writer). The old
SVGs were kept and compared as text.

The text difference is **exactly the keys row** in each file — nothing else changed:
- 80x24: two lines differ, both the footer row. The keys-row text line
  (`⏎ New session here / n New session / / Filter / Esc Back / z Fold / F Full view` → `⏎ Done / Esc Clear`) and the
  background rects on that same footer row (y=562.7), all `#39404f`, re-segmented for the shorter row.
- 160x45: two lines differ, both the footer row. The keys-row text line (same change) and the background rects on
  that same footer row (y=1075.1), all `#39404f`, re-segmented.

No panel content, no colour role, no geometry outside the keys row changed. Item 2 passes: the regenerated baselines
are green under `test_two_size_baselines` without the update flag.

### Item 1 — closing the panel (reverted to the build's behaviour by ruling)

The proposed rule (a close not from the rail hands the keyboard back to the session canvas) was implemented, tested
and stopped on twice: it broke two existing assertions that pin the rail-keeps-the-keys close, and after moving them
the tests' next step failed, because a post-close `enter` on a rail row needs the rail to hold the keys. The ruling
(2026-11-20 09:1x) settles it: **item 1 reverts to the build's behaviour** — a close keeps the keys on the rail when
the rail held them (the rail stays shown and focused), and hands the keys to the session when a list in the closed
panel held them. The reason, as ruled: with a session holding the canvas, the rail-keeps-the-keys close is the only
keyboard route to "Enter on a rail row opens the closed panel" (the redesign plan's Amendment 2, P2b, L1-rail-enter);
handing the keys to the canvas would leave that row reachable only by mouse.

The three files (`tsb_tui/screens/terminal.py`, `tests/test_redesign_focus.py`,
`tests/test_redesign_rail_tint_keys.py`) were restored to the committed branch with `git checkout --`: no `from_rail`,
no moved assertions, no new item-1 test. `close_sidebar` keeps `if not self._rail_has_keys(): self.set_focus(None)`.

### Commands (NO_COLOR unset, `PYTHONPATH=program/contract/client:.`, test venv python)

- Gate suite (`test_redesign_layout.py test_redesign_focus.py test_redesign_rail_tint_keys.py test_sidebar.py
  test_rail.py`): **215 passed** (exit 0) — the two regenerated baselines green, the focus and rail tests as committed.
- `make lint`: exit 0 (engine-check verified, import lint clean).

### Not verified

- No real terminal or PTY run; keys proven through the in-process fixture server.

## Fix round 3 (P2b review round 3)

**AI-Model:** swift-1.5 (Strata) | **Branch:** feat/tui-redesign-p2b (5c77d39) | **Date:** 2026-11-20
**Status:** DONE — both findings applied, one commit. No push.

The lane first stopped on the ruling's stop-condition: the side panel printed a number for a session not open
here (`terminal.py` `_sidebar_model` built `numbers` from the unfiltered chips; `sidebar.py` `_row_prefix` prints
it in the held-by-others group), and `tests/test_sidebar.py:91` pinned that row. The orchestrator ruled option (A)
(2026-11-20 10:1x): the side panel's `numbers` map is built from the same open-here, rail-ordered list the rail
and `^\ N` use; a row for a session not open here prints a blank number column.

### Item 1 — one number per session in this window (`terminal.py`)

- `switch_to_number`: N resolves against the rail's numbered list — the scan over the sessions open here, its
  `chip_ids` filtered to the open-here set (the same filter `_sync_fleet_widgets` applies), then `fleet.chips`
  over that scan copy. A session not open here has no digit: `^\ N` answers "No session is numbered N." and
  attaches nothing; such a session is reached from the side panel or the full view, as today.
- `_sync_fleet_widgets` / `_sidebar_model`: the sidebar's `numbers` map is built from `rail_chips` (the rail's
  own list), so a held-by-others row prints a blank number column. The full view already numbered only the
  sessions open here (`_fleet_view_model`'s `numbers` from `open_ids`; `_fleet_cells` prints "" otherwise) — no
  change there.
- `chip_number` (the drive `attach` answer's rail number, and the device-login events' `chip` field): reads the
  same open-here, rail-ordered list, so it reports the one number in this window. Every test that reads `chip`
  reads a session open here, so no existing assertion moved.

Moved assertion (per the ruling above): `tests/test_sidebar.py`,
`test_a_row_reads_who_types_the_login_that_types_and_rec_in_columns` — the held entry's `number=3` (the input the
assertion reads) dropped and line 91 moved from `" 3◆ lab-linux-04 minh      REC "` to
`"  ◆ lab-linux-04 minh      REC "` (blank number slot). Nothing else in that test changes.

### Item 2 — an ended session shows no keyboard notice (`terminal.py`)

- `_show_end`: when a slot's session ends, its keyboard notice clears at once (`keyboard_notice = ""`,
  `keyboard_notice_until = 0.0`).
- `refresh_status`: the frame renders the front slot's notice only while that slot has not ended
  (`front.ended is None` in the guard), so an ended front slot never shows "you type now".

### Tests first, red on 5c77d39 (both in `tests/test_redesign_rail_tint_keys.py`, keys only, 80x24 and 160x45)

The red run is `.lane-logs/red-r3.txt`: **4 failed** (exit 1).

| Test | Red line on 5c77d39 |
|---|---|
| `^\ N` resolves the rail's numbered list (x2) | `assert (SessionSlot(session_id='sess-6'…) is not None and 'sess-6' == 'sess-4')` — with `rail.chips=mine_held_by_others,open_here` and a peer-held session of mine sorted ahead, `^\ 1` left the front on the last-opened session (the held one gated the attach), not the rail's first row; `^\ 3` (the number the unfiltered list used to give the held session) attached the third-listed open session and raised no refusal |
| ended session shows no keyboard notice (x2) | `AssertionError: you type now: fixture-peer left` — `assert 'you type now…' == ''`: the notice lived on the ended slot, inside its 5s window |

Green: the same two tests, **4 passed** (exit 0). The scenario pins the knob through
`rail.chips=mine_held_by_others,open_here` so the held session sorts before the open ones in the unfiltered
list; under the default order the rail filter already kept digits 1..k aligned, and the digit past the open-here
count is where `^\ N` now refuses.

### Commands (NO_COLOR unset, `PYTHONPATH=program/contract/client:.`, test venv python)

- New tests red: 4 failed (exit 1, `.lane-logs/red-r3.txt`). Green: 4 passed (exit 0, `.lane-logs/green-r3-new.txt`).
- Gate suite (`test_redesign_layout.py test_redesign_focus.py test_redesign_rail_tint_keys.py test_sidebar.py
  test_rail.py test_fleet_view.py`): first pass **1 failed, 236 passed** (exit 1) — the moved `test_sidebar.py:91`
  string was one space short (the blank number slot keeps the mark's space); fixed, rerun: **237 passed**
  (exit 0, `.lane-logs/gate-r3.txt`).
- `make lint`: exit 0 (engine-check verified, import lint clean; `.lane-logs/lint-r3.txt`).

### Not verified

- No real terminal or PTY run; keys proven through the in-process fixture server.
- `tests/test_snapshots.py` and the full suite were not run (outside this round's gate); no baseline changed.
