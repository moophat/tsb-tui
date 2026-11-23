# Build report — redesign P4 (searchable New session and add target)

Branch `feat/redesign-p4-new-session`, baseline `86e11c2`, brief `plans/261116-1920-tui-redesign/phase-04-new-session.md` + Amendment 2 rows. **Model: strata/strata.**

**Status: DONE_WITH_CONCERNS.** The phase is implemented in `tsb_tui/screens/new_session.py` (now with the ruled `/` enter-filter and `a` add-target keys) and `tsb_tui/screens/target_form.py`; the new P4 suite (23 tests) is green. After this turn's G1 widening, the whole-directory run is **5 failed / 2611 passed / 3 skipped**: 4 are the `test_redesign_keys.py` STOP cases (the P1-era test asserts the retired dialog's single-Escape-to-workspace flow, which O3 rules as two Escapes — reported with options, not changed); 1 is `test_targets_tab.py`'s pre-existing #116 paging flake (fails on `86e11c2` too, left as ruled). The two load flakes (`test_drive_screen_lock.py`, `test_update_check.py`) did not reproduce. No commit, no push.

## Tests written first, red on 86e11c2

`tests/test_redesign_new_session.py` — 23 tests, key-driven Pilot against the contract's fixture server (DT7), asserting the ruled words in the painted screen; snapshots under `tests/snapshots/redesign-p4/`. All 23 fail on the baseline; first failing line per test (red-baseline3 run):

| test | first failing line |
|---|---|
| `test_search_matches_literal_name_and_host_text` | `test_redesign_new_session.py:83` — no row named `telnet scenario` in the painted screen |
| `test_match_beyond_the_first_page_shows_with_the_position` | `:119` — late-page match never painted; position title absent |
| `test_enter_on_a_target_creates_at_once_when_there_is_one_way` | `:137` — no row, and the create times out (Enter in an Input sends nothing) |
| `test_enter_on_a_telnet_target_creates_new_at_once` | `:188` — same: no row, no create |
| `test_a_live_connection_shows_the_choice_and_nothing_is_chosen_for_the_operator` | `:221` — the ask never appears; nothing is offered |
| `test_enter_on_a_target_you_have_a_session_on_asks_going_first` | `:248` (Q3) — no `go to session 1` row, no order, no highlight |
| `test_no_match_add_row_opens_the_form_prefilled_and_continues` | `:280` — no `+ Add 'router.example' as a target` row; the form never opens |
| `test_no_match_without_target_rights_says_the_ruled_words` | `:280` (joined copy) — the ruled no-rights copy is absent |
| `test_permission_revoked_after_the_form_opens_shows_the_refusal_and_creates_nothing` | conftest.py:122 — "timed out waiting for the target form" |
| `test_cancel_returns_exactly_one_level_with_the_query_intact` | conftest.py:122 — same timeout at the form |
| `test_two_size_baselines_for_the_ruled_states[matches|no-match|form|error|connection|credential × 80x24,160x45]` | first assertion of each case (row/word absent, form timeout, credential-choice timeout), then the missing baseline file |
| `test_retired_strings_and_the_direct_get_by_id_are_gone` | `:442` — `no such target` is on the screen and `GET /targets/no-such-device` was asked |

## Green after implementation

`tests/test_redesign_new_session.py` → **23 passed** (exit 0). The 10 functional tests pass; the 12 snapshot cases pass against the generated baselines under `tests/snapshots/redesign-p4/` (generated with `UPDATE_P4_SNAPSHOTS=1`, then verified without it).

Per acceptance row:
- **L1-filter-position** (`test_search_matches_literal_name_and_host_text`, `test_match_beyond_the_first_page_shows_with_the_position`): literal name/host-text search over the loaded pages, no DNS, no broker ask during a search; late-page match paints with the P3 position title. Green.
- **L1-open-direct** (`test_enter_on_a_target_creates_at_once_when_there_is_one_way`, `test_enter_on_a_telnet_target_creates_new_at_once`): Enter on a row creates at once (`connection: new`), telnet one-way too; the creator's size carried. Green.
- **§15.3 / Q3** (`test_a_live_connection_shows_the_choice_and_nothing_is_chosen_for_the_operator`, `test_enter_on_a_target_you_have_a_session_on_asks_going_first`): a live connection asks with nothing preselected; a session on this window asks `go to session 1` first and highlighted, then `another session`, `new connection`; choosing go brings the session forward and creates nothing. Green.
- **L1-new-target / O8** (`test_no_match_add_row_opens_the_form_prefilled_and_continues`): the no-match last row is exactly `+ Add '<query>' as a target`; Enter opens the existing form with the typed text in the address field when it reads as a host name, nothing else inferred; a successful save returns to New session with the saved record selected and creates nothing. Green.
- **L1-new-target-permissions** (`test_no_match_without_target_rights_says_the_ruled_words`, `test_permission_revoked_after_the_form_opens_shows_the_refusal_and_creates_nothing`): no rights → the ruled guidance, no Add row, no target-create; revoked rights → authoritative 403 in the form, no session, no auto retry. Green.
- **L1-focus / L1-escape / L1-geometry** (`test_cancel_returns_exactly_one_level_with_the_query_intact`, `test_two_size_baselines_for_the_ruled_states`): Cancel backs one level with the query intact; snapshots at 80x24 and 160x45 for matches/no-match/form/error/connection/credential; every control inside the viewport. Green.
- **L1-open-refusals (retirement)** (`test_retired_strings_and_the_direct_get_by_id_are_gone`): a no-match text is guidance, not `no such target`; no id required; no `GET /targets/{id}` asked. Green.

## Accepted refusal copy table (implemented)

The table lands in `#new-session-error` (the ruled row), with the core's own message under it in `#new-session-core`; recovery key is the person's own action, nothing retries by itself. `org_credential_locked` is the orchestrator's ruled row (`your passphrase cannot help` in the row, core message under), which keeps `test_vault_unlock.py` and `test_credential_choice.py` green unchanged.

| code | copy | recovery |
|---|---|---|
| `target_connect_failed` | `Could not connect to {label} · the target did not accept the connection · r tries again` | `r` |
| `target_auth_failed` | `The target refused the login · check the username and password · Enter tries again` | `Enter` |
| `hostkey_changed` | `The host key changed for {label} · nothing was opened · Esc closes` | `Esc` |
| `no_resolving_credential` | `No saved login fits this target · type the username and password · Enter tries again` (focuses username) | `Enter` |
| `vault_locked` | `Your vault is locked · type your passphrase · Enter unlocks and repeats once` | `Enter` |
| `org_credential_locked` | `The admin who holds that saved login has not unlocked it · your passphrase cannot help · ask the admin · Esc closes` | `Esc` |
| `mapped_credential_missing` | `The saved login this target maps to is gone · pick another, or type a username and password · Enter tries again` | `Enter` |
| `device_not_trusted` | `This device is not trusted for that login · nothing was opened · Esc closes` | `Esc` |
| `target_not_found` | `{label} is no longer in your list · r refreshes the list` (after a pick from the list; the no-match wording is for a query) | `r` |
| `inventory_unavailable` | `The broker's inventory is unavailable · r retries when it answers` | `r` |

## Geometry fix

At 80x24 the New session dialog clipped (form/error/connection/credential). Reduced the painted target-rows window to 3 rows (`#target-rows max-height: 3`, matching the `_paint_rows` slice) and compacted the filter, radio sets and login row; the TargetFormScreen inputs are single-line. Selection, position and `selected_target_id` stay correct for the reduced window. No control clips at 80x24 or 160x45.

## Gate (each to a file in `.lane-logs/`, exit code checked)

- `tests/test_redesign_new_session.py` → 23 passed, exit 0.
- Combined gate + widened suites (`test_new_session_login.py`, `test_target_form.py`, every `test_redesign_*.py`, `test_targets_tab.py`, `test_connection_choice.py`, `test_new_session_telnet.py`, `test_flow_pilot.py`, `test_stale_dialog.py`, `test_ended_and_reattach.py`, `test_drive_accounts.py`, `test_consumer_musts.py`, and the device-login callers) → **287 passed, 1 failed** in a single run; the one failure is `test_targets_tab.py` (see below). Re-run clean: `test_target_form.py` 14 passed exit 0; device-login callers 77 passed exit 0.
- `tests/test_targets_tab.py` → **1 failed** (`test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive`). This is a **pre-existing baseline failure**: with my `tsb_tui/` edits stashed on `86e11c2` the same suite fails (a paging/poll assertion, not touched by this lane). Not introduced here; **not verified green by me.**
- `make contract-check` → exit 0 (`program/ is clean at c4c2b81bb7a1, v0.18.0`).
- `make lint` → exit 0 (import lint, engine check, docs-branch guard). **ruff is not installed on this machine** — the repo's own lint targets ran; Python style was not checked.

## Out of scope — files that drive the retired entry (now widened and fixed)

The two suites that drove the retired `#target-id` Input and called the removed `_attempt_create()` were widened into G1 by the orchestrator's last ruling, and are now adapted under the same rule (target picked by the search path via `opened`, a Create press through the screen in place of `_attempt_create()`, an in-file `GET /targets` route; no assertion changed):
- `tests/test_remember.py` (`test_remember_saves_after_a_201_for_phase_a_fields`)
- `tests/test_login_save_lifecycle.py` (`test_a_create_remembers_only_the_login_and_choice_it_submitted[False]`, `[True]`)

`tests/stub_server.py` is not an allowed file; per G1 the `/targets` route was added inside the test files that need it, not in `stub_server.py`.

## Left

- `git diff --name-only`: `tsb_tui/screens/new_session.py`, `tsb_tui/screens/target_form.py`, and the widened test files (`device_login_support.py`, `test_connection_choice.py`, `test_new_session_telnet.py`, `test_flow_pilot.py`, `test_stale_dialog.py`, `test_ended_and_reattach.py`, `test_drive_accounts.py`, `test_consumer_musts.py`, `test_new_session_login.py`). Untracked additions: `tests/test_redesign_new_session.py`, `tests/snapshots/redesign-p4/`, this report. No out-of-scope path changed; no commit, no push.
- Snapshot baselines generated and reviewed against their rule (matches, no-match, form, error, connection, credential at 80x24 and 160x45).

## Ruled on your report — widening + final gate (the orchestrator's last ruling, 2026-11-23 14:2x)

G1 was widened to `tests/test_remember.py` and `tests/test_login_save_lifecycle.py`; both were adapted under the same rule (target picked by the search path via `opened`, a Create press through the screen in place of `_attempt_create()`, an in-file `GET /targets` route; no assertion changed). `test_targets_tab.py`'s failure was ruled pre-existing (tsb-tui #116) and left.

Every gate ran in the foreground against fixtures and test doubles only, `env -u NO_COLOR`, interpreter `$(PYTHON)`, each to a file in `.lane-logs/`, exit code checked, never through a pipe.

| run | command | result | exit |
|---|---|---|---|
| two widened suites | `$(PYTHON) -m pytest tests/test_remember.py tests/test_login_save_lifecycle.py` | 22 passed | 0 |
| new P4 suite | `$(PYTHON) -m pytest tests/test_redesign_new_session.py` | 23 passed | 0 |
| whole directory | `$(PYTHON) -m pytest tests/` | 19 failed, 2597 passed, 3 skipped (57 min) | 1 |

`test_targets_tab.py` **passed in this whole-directory run** (no failure; the pre-existing #116 paging flake did not reproduce here).

### The 19 whole-directory failures, each against `86e11c2`'s code

To answer "does it also fail on `86e11c2`'s code", all tracked changes were stashed to the clean baseline (`86e11c2`) and the eight failing files re-run: **all 185 tests in them pass at baseline** (`.lane-logs/baseline-failures.txt`, exit 0). So **none of the 19 fails on `86e11c2`'s code** — every one is introduced by this lane's `new_session.py` change interacting with a test file not adapted for P4, and every one is outside my allowed files for this turn.

| file | failing test(s) | reason in this run | fails on 86e11c2? |
|---|---|---|---|
| `test_attach_confirm.py` | 6 (host-key tick / recording gate, later-attach status row, every-attach gate, re-attach gate, no_resolving_credential as a state) | drives the retired entry: `open_new_session` + Create press expects the attach gate; the new screen needs a loaded inventory row and the stub has no `GET /targets` route, so the create stays on New session | No (passes at baseline) |
| `test_ended_and_reattach.py` | 1 (`test_esc_closes_the_dialog_and_the_row_keeps_the_end_with_new_and_targets`) | widened file, adaptation incomplete — its stub has no `GET /targets` route, inventory times out | No |
| `test_flow_pilot.py` | 1 (`test_full_flow_stays_responsive_through_a_slow_chunked_attach`) | widened file, adaptation incomplete — inventory times out | No |
| `test_new_session_telnet.py` | 2 (no-match query creates nothing / second Create press reaches no broker) | widened file, adaptation incomplete — inventory times out | No |
| `test_reattach_login.py` | 1 (`test_a_create_that_completes_after_the_login_is_gone_is_discarded`) | drives the retired entry; inventory times out so `_on_create_done` never runs | No |
| `test_redesign_keys.py` | 6 (`test_f1_menu_search_focus_and_hints` ×2 sizes, `test_inventory_n_and_permission_gated_a` ×4) | P1-era snapshot baselines and the retired dialog's key behaviour (the new screen has no `/`/`a` key bindings, so `fleet_view.editing` is not set) changed by this lane | No |
| `test_drive_screen_lock.py` | 1 (`test_any_command_restarts_the_idle_time`) | load flake under the full-suite run — passes isolated in this tree (`.lane-logs/isolated-flake-check.txt`, exit 0) and at baseline; unrelated to `new_session.py` | No |
| `test_update_check.py` | 1 (`test_slow_request_does_not_block_login_and_worker_is_bounded`) | load flake under the full-suite run — passes isolated in this tree (exit 0) and at baseline; unrelated to `new_session.py` | No |

The widened files (`test_ended_and_reattach.py`, `test_flow_pilot.py`, `test_new_session_telnet.py`) are in G1's list, so their remaining inventory-timeout tests need the same in-file `GET /targets` route as the device-login suites; this turn's instruction restricted me to `tests/test_remember.py`, `tests/test_login_save_lifecycle.py` and this report, so I did not touch them. The non-widened files (`test_attach_confirm.py`, `test_reattach_login.py`, `test_redesign_keys.py`) are not in the brief's allowed list, so they were reported, not fixed.

### Left after this turn

- New changes this turn: `tests/test_remember.py`, `tests/test_login_save_lifecycle.py` (target-picking + Create press + in-file `/targets` route; no assertion changed) and this report. `git diff --name-only` lists only the prior-turn accepted files plus these two; no out-of-scope path touched. No commit, no push.
- `.lane-logs/` deleted at the end.

## Ruled on your whole-suite run — fix + final gate (this turn, 2026-11-23)

The orchestrator ruled the 17 non-flake failures this phase's to fix, widened G1 again, and told me `test_redesign_keys.py` is not an adaptation. I fixed what a ruling explains, regenerated the retired-dialog snapshots, and STOPped on the one assertion no ruling explains. No commit, no push.

### G1 widening (target picking + Create press + in-file `GET /targets` route; no assertion changed)

Adapted under the same rule as the prior turns: a stub without a `GET /targets` list route gains one inside the test file; a test picks its target by the search path (the prefilled row `opened`/`open_new_session` selects from the loaded inventory, read via `selected_target_id`) and presses Create through the screen. Files this turn:
- `tests/test_attach_confirm.py` — `_ListApp(SlowChunkedApp)` with a `/targets` route returning `stub-target`; all six stubs use it. The 6 attach-gate / recording / no_resolving_credential tests drive New session and now load the inventory, so Create reaches the gate.
- `tests/test_reattach_login.py` — the `/targets` route added to `LateCreateStub`; `test_a_create_that_completes_after_the_login_is_gone_is_discarded` presses Create through the screen on the loaded row, so `_on_create_done` runs and its post-login discard is asserted.
- `tests/test_ended_and_reattach.py` — `_ListApp` added, `_closing()` returns it; `test_esc_closes_the_dialog_and_the_row_keeps_the_end_with_new_and_targets` picks `stub-target` by the search path (`selected_target_id`).
- `tests/test_flow_pilot.py` — `_ListApp` added and used in `_run_flow`; the prefilled target is picked by the search path.
- `tests/test_new_session_telnet.py` — `_ListApp` added (keeps the configured `GET /targets/{id}` error); the two no-match stub tests load the inventory, type a query that matches no row, and press Create through the screen (creates nothing).

No assertion was changed; only target picking, the Create press, and the in-file route.

### `tests/test_redesign_keys.py` — 6 failures, classified per the ruling

The 6 failures split by behaviour:

- **2 snapshot of the retired dialog's look** (`test_f1_menu_search_focus_and_hints[80x24]`, `[160x45]`, first failing line `test_redesign_keys.py:38` — the SVG compare): the menu opened over the New session screen (context `new-session`) baselines `menu-new-session-80x24.svg` and `menu-new-session-160x45.svg` show the retired target-id dialog. This lane's searchable New session looks different, so those two baselines are regenerated (`UPDATE_P1_SNAPSHOTS=1`), then verified without it. The report names them: `tests/snapshots/redesign-p1/menu-new-session-80x24.svg`, `menu-new-session-160x45.svg`. The `menu-workspace-*` and `menu-list-*` baselines are unchanged (this lane does not touch the workspace or the full-view list).

- **A ruled behaviour the new screen lacked, fixed in `new_session.py`**: Ruled pick 4 / O3 require `/` to enter the filter and `a` to add a target (shown only with `target.edit`) in every inventory list, and Amendment 1 rule 2.5 makes `a` open the target form from the New session screen's no-match row. The new screen had neither key. I added `Binding("slash", "focus_filter", "Filter")` and `Binding("a", "add_target", "Add target")` (non-priority: typing while the filter holds the keys, acting on the dialog's controls), with `action_focus_filter` (focuses `#new-session-filter`) and `action_add_target` (`_open_form()` when `self._rights()`, i.e. `target.edit`; nothing without it). These are the ruled behaviours; the test was not changed.

- **STOP — an assertion neither a ruling nor the retired dialog's look explains** (`test_inventory_n_and_permission_gated_a`, all 4 cases): the test does `press("escape", "a")` then asserts `isinstance(app.screen, TargetFormScreen) is permission` (line 120), and `press("/", "n", "a")` then asserts `workspace.fleet_view.editing` and `workspace.fleet_view.filter_text.endswith("na")` (lines 125, 128). These pass on `86e11c2` only because the retired dialog's **single** Escape popped New session back to the workspace, so `a` and `/ n a` reached the fleet_view (which handles `slash`→editing and `a`→target-new). O3 rules the New session's Escape as: **first Esc clears the filter, a second Esc goes back one level** — so one Escape does not pop, and `a`, `/` and typing stay in New session. The test asserts the retired single-Escape-to-workspace flow applied to fleet_view; that is not a ruled behaviour for the new screen (O3 mandates two Escapes) and not a snapshot. I did not change the test. **Options:**
  1. Re-assert the ruled flow in the test: press Escape twice to leave New session, then `a` / `/ n a` reach fleet_view — a key-press-path change to the P1-era test (needs the orchestrator's ruling; the ruling says "never by changing the test" for a ruled behaviour).
  2. Keep the new screen's two-Escape rule (O3) and leave this P1-era test as a known conflict to be re-ruled — it encodes the retired dialog's single-Escape pop.

After adding the bindings and regenerating the two snapshots, `test_redesign_keys.py` is 16 passed, 4 failed (the STOP cases: `[80x24-True]`,`[160x45-True]` at line 120; `[80x24-False]`,`[160x45-False]` at line 125).

### Load flakes (named, left)

`test_drive_screen_lock.py::test_any_command_restarts_the_idle_time` and `test_update_check.py::test_slow_request_does_not_block_login_and_worker_is_bounded` are load flakes under the full-suite run (each passes isolated in this tree and at baseline; unrelated to `new_session.py`). **Neither reproduced in this turn's whole-directory run** — both passed.

### The final whole-directory run, each failure against `86e11c2`'s code

`env -u NO_COLOR $(PYTHON) -m pytest tests/` → **5 failed, 2611 passed, 3 skipped** (57 min). To answer "does it also fail on `86e11c2`'s code", all tracked changes were stashed to the clean baseline and the two failing files re-run: `tests/test_redesign_keys.py tests/test_targets_tab.py` → **1 failed, 34 passed** (`.lane-logs/baseline-keys-targets.txt`, exit 1).

| file | failing test(s) | fails on 86e11c2? |
|---|---|---|
| `test_redesign_keys.py` | `test_inventory_n_and_permission_gated_a[80x24-True]`,`[160x45-True]` (line 120), `[80x24-False]`,`[160x45-False]` (line 125) | **No** — the keys suite passes fully on `86e11c2`'s code. Introduced by this lane's `new_session.py`; the STOP cases above. |
| `test_targets_tab.py` | `test_a_click_on_the_last_loaded_target_selects_it_while_pages_arrive` (line 386, "a poll ran while a page was in flight") | **Yes** — fails on `86e11c2` too. Pre-existing tsb-tui #116 paging flake; not introduced here, left as ruled. |

The two load flakes did not reproduce. No other file failed.

### Gate (each to a file in `.lane-logs/`, exit code checked, foreground, `env -u NO_COLOR`, interpreter `$(PYTHON)`)

| run | command | result | exit |
|---|---|---|---|
| keys before changes | `$(PYTHON) -m pytest tests/test_redesign_keys.py` | 6 failed, 14 passed | 1 |
| 5 G1-adapted suites | `$(PYTHON) -m pytest tests/test_attach_confirm.py tests/test_reattach_login.py tests/test_ended_and_reattach.py tests/test_flow_pilot.py tests/test_new_session_telnet.py` | 46 passed | 0 |
| regen the two retired-dialog snapshots | `UPDATE_P1_SNAPSHOTS=1 $(PYTHON) -m pytest tests/test_redesign_keys.py::test_f1_menu_search_focus_and_hints` | 2 passed | 0 |
| keys after changes | `$(PYTHON) -m pytest tests/test_redesign_keys.py` | 16 passed, 4 failed (STOP cases) | 1 |
| new P4 suite | `$(PYTHON) -m pytest tests/test_redesign_new_session.py` | 23 passed | 0 |
| whole directory | `$(PYTHON) -m pytest tests/` | 5 failed, 2611 passed, 3 skipped (57 min) | 1 |
| baseline for the two failing files | `git stash push -- tsb_tui/ tests/` then `$(PYTHON) -m pytest tests/test_redesign_keys.py tests/test_targets_tab.py` | 1 failed (targets_tab), 34 passed | 1 |
| contract-check | `make contract-check` | program/ clean at c4c2b81bb7a1, v0.18.0 | 0 |
| lint | `make lint` | import lint, engine check, docs-branch guard pass; **ruff not installed** — Python style not checked | 0 |

### Left after this turn

- Changes this turn: `tsb_tui/screens/new_session.py` (`/` and `a` bindings + actions), the two regenerated `menu-new-session-*` P1 snapshots, `tests/test_attach_confirm.py`, `tests/test_reattach_login.py`, `tests/test_ended_and_reattach.py`, `tests/test_flow_pilot.py`, `tests/test_new_session_telnet.py` (target picking + Create press + in-file `/targets` route; no assertion changed), and this report. `git diff --name-only` lists only the prior-turn accepted files plus these. No out-of-scope path touched.
- **Not green, not fixed:** 4 `test_redesign_keys.py` cases STOPped on the single-Escape-to-workspace assertion (options above); `test_targets_tab.py`'s #116 flake left as ruled. `.lane-logs/` deleted at the end. No commit, no push.

## Ruled on your keys stop — Option 1 applied (this turn, 2026-11-23)

The orchestrator ruled **Option 1**: O3 rules New session's Escape (first leaves and clears the filter, a second goes back one level), so the STOP is resolved as a key-path adaptation under G1's rule — how the test closes New session changes, never what it asserts.

`test_inventory_n_and_permission_gated_a`: the retired dialog needed one Escape to pop New session back to the inventory list; O3 needs two. `tests/test_redesign_keys.py:118` changed from `press("escape", "a")` to `press("escape", "escape", "a")`. The first Escape clears the filter (`action_cancel`: `if self._filter_text: ...value = ""; return`), the second pops the screen back one level to the fleet_view, where `a` opens the target form (with `target.edit`) and `/ n a` reaches the filter. **Every assertion is unchanged** (line 120 `isinstance(app.screen, TargetFormScreen) is permission`; lines 125, 128 `fleet_view.editing` and `filter_text.endswith("na")`). Only the key path that reaches the state changed; no ruled behaviour was altered. Allowed files this turn: `tests/test_redesign_keys.py` and this report.

### Gate (foreground, `env -u NO_COLOR`, interpreter `$(PYTHON)`, each to a file in `.lane-logs/`, exit code checked, never through a pipe)

| run | command | result | exit |
|---|---|---|---|
| keys before the key-path change | `$(PYTHON) -m pytest tests/test_redesign_keys.py` | 4 failed, 16 passed — first failing line `test_redesign_keys.py:120` (`assert False is True`: after one Escape the screen is still `NewSessionScreen`, not `TargetFormScreen`) | 1 |
| keys after the key-path change | `$(PYTHON) -m pytest tests/test_redesign_keys.py` | **20 passed** | 0 |
| new P4 suite | `$(PYTHON) -m pytest tests/test_redesign_new_session.py` | **23 passed** | 0 |

The whole directory was not re-run this turn (per instruction). The 4 STOP cases are now green; no new failures. `test_targets_tab.py`'s #116 flake stays as ruled, untouched. `.lane-logs/` and `.t/` deleted at the end. No commit, no push.

## Lint before the PR (this turn, 2026-11-23)

`make lint` reported 8 ruff errors in this phase's files. All fixed without behaviour change. Allowed files this turn: `tsb_tui/screens/new_session.py`, `tests/test_redesign_new_session.py`, `tests/test_device_login_pane.py`, and this report.

### The 8 fixes

| rule | file:line | fix | behaviour |
|---|---|---|---|
| I001 | `tests/test_device_login_pane.py:2` | sort the import block (`import device_login_support` before `import pytest`) | none |
| I001 | `tests/test_redesign_new_session.py:12` | sort the import block (`from textual.widgets import Select` into the third-party group) | none |
| I001 | `tsb_tui/screens/new_session.py:23` | reformat `from textual.containers import Horizontal, Vertical` to the parenthesised multi-line form | none |
| RUF100 | `tsb_tui/screens/new_session.py:30` | drop the unused `# noqa: F401` — `Horizontal`/`Vertical` are used in `compose` (`new_session.py:217,230,240,243`), so F401 does not fire | none |
| F841 | `tsb_tui/screens/new_session.py:273` | remove the dead `line = self._row_line(row)` in `_select_initial` (see below) | none |
| SIM102 | `tsb_tui/screens/new_session.py:384` | combine the nested `if not rows and query:` / `if not self._rights():` into one `and`; short-circuit is identical | none |
| RUF059 | `tsb_tui/screens/new_session.py:556` | prefix the unpacked, unused `label` as `_label` in `_act_ask` (only `kind`/`arg` are read) | none |
| RUF059 | `tests/test_redesign_new_session.py:339` | prefix the unpacked, unused `served` as `_served` (this test never calls `served.cancel()`; `url` is used) | none |

The three I001 and the RUF100 are ruff's safe fixes, applied with `ruff check --no-cache --fix` on exactly those three files. The F841, SIM102 and two RUF059 are ruff's unsafe fixes, done by hand.

### F841 `line` — read before dropping (the brief's required check)

`_select_initial` computed `line = self._row_line(row)` then updated `#new-session-target` with `f"{as_text(self._initial_target)} · {as_text(self._types.get(...))}"` — the `id · type` string, not `line`. `_row_line` is a pure read (it builds a label + facts string; no widget write, no state change), and its result was never consumed. The same target line is set independently by `_show_type` (`new_session.py:599`), which runs right after in `on_mount`. Git blame confirms `line = self._row_line(row)` is uncommitted P4 work and `_select_initial` does not exist on `86e11c2`. Dropping it changes nothing shown or done, and reveals no missing use — so the brief's STOP condition is not met and the assignment was removed.

### Gate (foreground, `env -u NO_COLOR`, interpreter `$(PYTHON)`, each to a file in `.lane-logs/`, exit code checked, never through a pipe)

| run | command | result | exit |
|---|---|---|---|
| make lint | `make lint` | fails on the root-owned `.ruff_cache` — `Permission denied ... .ruff_cache/0.16.9/.tmp*` (the cache dir is owned by root, created when the orchestrator ran as root; not a code error, not in an allowed file) | 2 |
| ruff check (identical scope, no cache) | `ruff check --no-cache tsb_tui tests bench tools` | **All checks passed** — the same check `make lint` runs, with the 8 errors gone | 0 |
| keys suite | `$(PYTHON) -m pytest tests/test_redesign_keys.py` | **20 passed** | 0 |
| new P4 suite | `$(PYTHON) -m pytest tests/test_redesign_new_session.py` | **23 passed** | 0 |

`make lint`'s exit 2 is the environment's root-owned cache, not a lint failure: the identical ruff check on the same scope passes clean via `--no-cache`. The two suites that cover this phase's files stay green. `.lane-logs/` deleted at the end. No commit, no push.
