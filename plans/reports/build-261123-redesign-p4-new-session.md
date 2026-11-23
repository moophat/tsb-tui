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

## Qodo round 1 — tsb-tui PR #117 (this turn, 2026-11-23)

Qodo's 15 findings on `tsb_tui/screens/new_session.py` at `ef98743`. Each: a verdict from the code and the phase's rulings; if real, a fix in `new_session.py` with a test in `tests/test_redesign_new_session.py` red on `ef98743` (first failing line recorded) then green; if wrong, a refuting line; if the right behaviour is not settled by the phase file, plan rulings or the sections above, STOP with options. No new key or copy beyond the ruled ones. Allowed files: `tsb_tui/screens/new_session.py`, `tests/test_redesign_new_session.py`, this report.

### Verdicts

**Fixed (real), red-first then green:**

- **1 — New session can open the wrong target (prefilled target absent from the loaded inventory).** Real. `_select_initial` left `_sel` at 0 and Create read the first loaded row. The ruled rule "selection follows the entity" (P3) settles the fix: the highlight is a target identity (`_sel_id`), kept as the supplied target until the operator moves onto a row; Create, the Enter press, `_show_type`, device-login and `selected_target_id` all act on that id, never the row at a stale index. Red first failing line: `test_redesign_new_session.py:482` — `assert 'alt-screen' == 'ghost-target'` (Create sent the first loaded row). Green: `test_a_prefilled_target_not_in_the_loaded_list_is_created_not_the_first_row`.
- **3 — Enter opens the target from every control.** Real. The screen-wide `enter` binding was `priority=True`, so it took Enter from a focused Cancel, device-login, Remember, the picker and a credential choice. Fix: drop `priority`; `⏎` in the filter is handled by `on_input_submitted` (the filter's submit), and Enter on any other focused control belongs to that control. Red first failing line: `test_redesign_new_session.py:534` — `assert 1 == 0` (Enter on Cancel started a create). Green: `test_enter_on_a_focused_control_is_not_the_open_press`.
- **4 — Pressing enter twice creates two sessions.** Real. `_create_from_row`/`action_act_row` disabled Create but never checked a create in flight. Fix: a `_creating` flag set when the create is sent (`_create`), guarding `action_act_row` and `_create_from_row`, cleared in `_on_create_error` and `_finish_create_done`, kept across `_unlock_then_repeat`. Red first failing line (create held on the broker): `test_redesign_new_session.py` — `assert 2 == 1` (`stub.creates`). Green: `test_a_second_enter_during_a_create_in_flight_sends_one_create`.
- **5 — A failed create over the network shows nothing.** Real. `_on_create_error` only showed `ApiError` codes; an `HttpError`/`OSError` re-enabled Create with no message. Fix: a fallback that writes `str(exc)` to `#new-session-error` and calls `self.app.notify_api_error(exc)` when `not isinstance(exc, ApiError)` (the app's own transport handling). No new copy. Red first failing line: `test_redesign_new_session.py:591` — `assert 'connection refused' in joined`. Green: `test_a_transport_failure_shows_the_operator_something`.
- **7 — A new-connection choice can request reuse.** Real. `_act_ask` passed no mode for "new connection", so `_start_create` read the reuse radio left from before. Fix: each ask option carries its own mode through `_create_from_row` to `_start_create` (`connection_mode`), independent of the radio. Red first failing line: `test_redesign_new_session.py:627` — `assert 'reuse' == 'new'`. Green: `test_choosing_new_connection_after_the_reuse_radio_sends_new`.
- **8 — Arrow selection leaves login controls stale.** Real. `action_row_up`/`action_row_down` moved `_sel` and repainted without `_show_type`, so moving between SSH and telnet rows kept the prior row's login fields / reuse state. Fix: call `_show_type` on every row-selection change. Red first failing line: `test_redesign_new_session.py:646` — `assert True is False` (the telnet row kept the SSH login fields). Green: `test_moving_the_highlight_updates_the_protocol_controls`.
- **13 — An ask answer can open the wrong target.** Real. `_act_ask` re-derived the target by index into `_matched_rows()` (crashes on an empty list, opens a different row if the list changed under the ask). Fix: `_open_target` saves `self._ask_target = target_id`; `_act_ask` creates on that id with the option's mode. The same fix carries the reuse option's connection id for the asked target. Covered by `test_choosing_new_connection_after_the_reuse_radio_sends_new` (the reuse/new path acts on the asked target); no standalone red, because the index re-derivation only differs when the inventory changes under an open ask, which the fixture cannot drive deterministically.
- **14 — "go to session n" can name the wrong number.** Real. `_open_target` counted over closed-filtered slots; the rail and `_open_numbers` count over all of `workspace.slots` (closed included). Fix: numbers are `[index+1 for index, slot in enumerate(workspace.slots) if not slot.closed and slot.target == target_id]`, and the session id is `workspace.slots[first-1].session_id`, matching `_open_numbers` and the rail. Red first failing line: `test_redesign_new_session.py:679` — `assert 'go to session 2' in joined`. Green: `test_the_ask_number_counts_closed_slots_like_the_rail`.
- **15 — Telnet targets offer a reuse that is silently dropped.** Real. `_open_target` built the "another session" option for any live connection, ignoring telnet; `_start_create` then forced `new` (DEFAULT 22: a telnet connection carries one session). Fix: `live = [] if self._is_telnet(target_id) else self._live_connections(target_id)`, so a telnet target never gets the reuse ask. Red first failing line: `test_redesign_new_session.py` — timed out waiting for the one-way create (the ask was shown on the baseline). Green: `test_a_live_telnet_connection_open_one_way_not_an_ask`.

**Fixed, real, no standalone red test:**

- **6 — Open sessions can lose their reuse choice.** Real. `_sessions_for` called `fleet.scan` without the workspace's live records, so a session opened in this window counted only after the fleet poll listed it. Fix: pass `live=workspace._live()` into the scan (the same read the workspace makes for its other views). Green: `test_a_session_opened_in_this_window_offers_another_session_before_the_poll`. **Not red on `ef98743`** — in that run the fleet poll lists the opened session before the ask is raised, so the baseline test also passes; the fix is a correctness improvement for the window between open and poll, verified by code, not by a red-first assertion.

**Refuted (the finding is wrong):**

- **2 — Operators cannot see connection choices.** Refuted. `_on_choice_sessions` shows the picker inside `#reuse-box`, and `on_radio_set_changed` sets `#reuse-box` display True when the reuse radio is pressed (`new_session.py:617`). `CONNECTION_CHOICE_REQUIRED` only follows a create that requested reuse, which is the conn-reuse radio (the only reuse path in this screen); the ask's reuse carries a connection id, so it never lands on the 409. So the box is already visible when the picker populates — the picker is not left inside a hidden container. The test `test_the_connection_choice_picker_is_shown_not_inside_a_hidden_box` passes on `ef98743` (the box is visible), confirming the refutation. I kept the defensive `#reuse-box` show/restore in `_on_choice_sessions` and `on_select_changed`; it is harmless and keeps the box's resting visibility correct.

**STOP — the right behaviour is not settled by the phase file, plan rulings, or the sections above (fixing the rest meanwhile):**

- **9 — Credential choices cannot use the keyboard.** Real. `_offer_credential_choice` shows the candidate RadioSet but does not focus it, so keyboard Enter acts on the target row, not a candidate. Focusing the choice is the natural fix, and I tried `choice.focus()`: it makes the L1-focus credential snapshot (`credential-80x24.svg`, `credential-160x45.svg`) **non-deterministic** — the focused RadioSet's highlight bar blinks, so the frozen baseline differs run to run (the two baselines failed intermittently; regenerating one size then running the other failed). Giving keyboard access without a blinking focus highlight is a design decision the rulings do not settle. **Options:** (1) focus the credential choice and make the focus highlight static for snapshots (a rendering change), then regenerate the two baselines [recommended — it satisfies L1-focus keys-only traversal and keeps the baseline stable]; (2) keep the choice unfocused and route Enter to it through a key path that does not focus the blinking RadioSet (not standard keys-only traversal); (3) leave the choice as-is (mouse-only pick) and record finding 9 as a known gap. I did not change the code for this item; the credential baselines are unchanged.
- **10 — A failed refresh keeps polling forever.** Real. `_after_form` starts a repeating 100 ms timer (`_check_pending`) that returns indefinitely if the saved row never lands; a failed inventory refresh after a successful save leaves the screen polling with no failure shown. **Options:** (1) bound the watch to N ticks and then show an actionable inventory state retaining the saved target id; (2) stop the watch on the workspace's refresh failure. The number of ticks, the state shown, and how New session learns of the refresh failure are not specified. No new copy exists for "the add-target save landed but the list did not refresh"; `inventory_unavailable` is about the broker's inventory, not this continuation. I did not change the code.
- **11 — The refusal recovery keys don't do what the message says.** Real. `target_connect_failed`/`target_not_found`/`inventory_unavailable` copy names `r`, but `r` is a non-priority binding to `action_refresh_list` (refetches the list, never repeats a create), and `on_mount` focuses the filter, so `r` types into the filter after a refusal. **Options:** (1) move focus off the filter when a refusal with an `r` recovery shows, and make `r` act on the refusal code (repeat `_create_from_row(self._pending_target_id)` for `target_connect_failed`; refetch + repaint for `target_not_found`/`inventory_unavailable`); (2) change the copy to name a key that performs the stated action. The focus target (Create button vs a dedicated recovery state) and whether `r` repeating a create is "the person's own action" (the ruled table says every recovery key is, and nothing retries by itself) are design decisions the rulings do not settle. I did not change the code.
- **12 — Enter can open a target other than the highlighted one.** Split. **Fixed part:** the stale-index correctness (Create/Enter opens the row that now sits at `_sel`) is fixed by selection-by-id (finding 1). **STOP subpart:** the finding also asks to repaint the list when `workspace.targets.rows` changes under the screen (pages after `n`, the `r` refresh, a background poll). The selection stays on the entity, so the mark tracks it, but the painted window is not refreshed between key events. The repaint mechanism (a workspace callback/message vs a light `set_interval`) and its trigger are not settled by any ruling. I did not add a repaint timer.

### Gate (foreground, `env -u NO_COLOR`, interpreter `$(PYTHON)`, each to a file in `.lane-logs/`, exit code checked, never through a pipe)

| run | command | result | exit |
|---|---|---|---|
| new P4 suite (fixed) | `$(PYTHON) -m pytest tests/test_redesign_new_session.py` | **33 passed** (23 + 10 new) | 0 |
| red on `ef98743` (new_session.py stashed) | `$(PYTHON) -m pytest tests/test_redesign_new_session.py` | the 10 finding tests fail (first failing lines above); the 23 phase tests pass | 1 |
| keys suite | `$(PYTHON) -m pytest tests/test_redesign_keys.py` | **20 passed** | 0 |
| login suite | `$(PYTHON) -m pytest tests/test_new_session_login.py` | **13 passed** | 0 |
| connection-choice suite | `$(PYTHON) -m pytest tests/test_connection_choice.py` | **7 passed** | 0 |
| credential-choice suite | `$(PYTHON) -m pytest tests/test_credential_choice.py` | **14 passed** | 0 |
| combined gate | `$(PYTHON) -m pytest tests/test_redesign_new_session.py tests/test_redesign_keys.py tests/test_new_session_login.py tests/test_connection_choice.py tests/test_credential_choice.py` | **87 passed** | 0 |
| make lint | `PATH=$(VENV_BIN):$PATH make lint` | fails on the root-owned `.ruff_cache` (environment, not code) | 2 |
| ruff check (identical scope, no cache) | `PATH=$(VENV_BIN):$PATH ruff check --no-cache tsb_tui tests` | **All checks passed** | 0 |

### Not verified / left

- Findings 9, 10, 11 and the finding-12 repaint subpart are STOPped with options above; no code change for them.
- Finding 6's test is not red on `ef98743` (the fleet poll lists the session before the ask); the fix is verified by code, not a red-first assertion.
- Finding 2 is refuted; its test passes on `ef98743`.
- No snapshot baseline changed (the credential focus change was reverted; the two credential baselines are unchanged).
- `git diff --name-only`: `tsb_tui/screens/new_session.py`, `tests/test_redesign_new_session.py` only. `.lane-logs/` deleted at the end. No commit, no push.

## Qodo round 2 — the ruled stops (this turn, 2026-11-23)

The orchestrator ruled findings 9, 10, 11, 12 and the CI timing failure. Each fix red on `ef98743` first (first failing line recorded), then green. Allowed: `tsb_tui/screens/new_session.py`, `tests/test_redesign_new_session.py`, the two credential snapshot baselines, the snapshot harness only if the deterministic frame needs it, this report. No new key or copy beyond the ruled ones.

### Finding 9 — keyboard on the credential choice (ruled option 1) — FIXED

`_offer_credential_choice` now calls `choice.focus()` after showing the candidates, so keys-only traversal reaches them (the ruled focus rule). The focused RadioSet takes Enter/arrows; `down` highlights a candidate and `enter` toggles it, firing `RadioSet.Changed` → `_pick_credential`. No change to what a person sees.

Determinism: with focus added, the two credential snapshots (`credential-80x24.svg`, `credential-160x45.svg`) were non-deterministic when both sizes ran in one session — the focused control's highlight layout varies run to run. The ruling says make them deterministic **in the test**. The snapshot harness is `_p4_snapshot` in `tests/test_redesign_new_session.py`; I added a `await pilot.pause()` settle before `export_screenshot`, so the frozen frame is the resting one, not a mid-refresh snapshot. This is the snapshot harness (named: `tests/test_redesign_new_session.py`), not a product rendering change. It was deterministic in isolation before the settle; the settle makes it deterministic across a full parametrized session. Both baselines regenerated with the settled harness; the credential snapshot cases pass 5 runs in a row.

Keys-only test added: `test_the_credential_choice_takes_focus_and_a_key_picks_a_candidate` — asserts the offered choice takes focus (`app.screen.focused.id == "credential-choice"`) and `down`+`enter` picks a candidate, repeating the create with that credential. Red first failing line on `ef98743`: `test_redesign_new_session.py:848` — `assert app.screen.focused.id == "credential-choice"` → `Input(id='new-session-filter')`. Green on the fixed tree.

Baselines regenerated and named: `tests/snapshots/redesign-p4/credential-80x24.svg`, `tests/snapshots/redesign-p4/credential-160x45.svg` (the focused credential choice; every control stays inside the viewport).

### Finding 10 — the add-target watch (ruled option 1) — FIXED

`_check_pending` is bounded: `_pending_ticks` counts the 0.1 s watch ticks, and at `PENDING_WATCH_TICKS` (50 = 5 seconds after the save) the watch stops (`_stop_pending_watch`) and shows the Targets tab's own inventory-down copy, `TARGETS_UNAVAILABLE_COPY` = "Could not load targets · r retry" (the plan's existing copy, not new), on `#new-session-error`. The saved id (`_pending_select`) is kept, so a later successful list selects it. No endless timer, no new copy.

Test: `test_the_add_target_watch_bounds_and_shows_the_inventory_state` — a stub (`SaveThenDownApp`) saves the target then fails every `GET /targets`, so the saved row never lands; asserts the watch ends within 8 s, shows the inventory state, keeps `_pending_select == "new-gear"`, and `_pending_timer is None`. Red first failing line on `ef98743`: `tests/conftest.py:122` — `timed out after 8s waiting for the bounded watch to end` (the watch polls forever, no bound, no state). Green on the fixed tree. The "a later successful list selects it" half needs finding 12's notification (see below): the id is kept, and the select-on-landing repaint is gated by finding 12.

### Finding 11 — recovery keys (ruled option 1) — FIXED

When a refusal whose recovery key is `r` shows (`target_connect_failed`, `target_not_found`, `inventory_unavailable` — `R_RECOVERY`), `_on_create_error` records `self._recovery_code` and moves the keyboard off the filter to the Create button (`#create-button.focus()`), so `r` reaches the binding, not typing. `action_refresh_list` acts on the code: `target_connect_failed` repeats `_create_from_row(self._pending_target_id)` through the create path (the in-flight guard applies); `target_not_found`/`inventory_unavailable` refetch the list and repaint. With no such refusal on screen, `r` is the ordinary foreground read. `_recovery_code` clears on a new create. Nothing retries by itself; every recovery is the person's own press.

Tests:
- `test_r_after_a_connect_refusal_repeats_the_create` — red first failing line `test_redesign_new_session.py:751` (`assert app.screen.focused.id == "create-button"` → filter); green: after a `target_connect_failed`, focus is on Create and the person's `r` sends a second create on `normal`.
- `test_r_after_a_not_found_refusal_refetches_the_list` — red first failing line `test_redesign_new_session.py:775` (same focus assertion); green: after a `target_not_found`, focus is on Create and the person's `r` issues a new `GET /targets`.

The "refetches **and repaints**" for `target_not_found`/`inventory_unavailable`: `r` refetches and calls `_paint_rows()` (the press repaints); the refreshed rows land from the async refetch, and painting them as they land is finding 12's notification (see below), gated by the same constraint.

### Finding 12 — repaint on new rows — STOP (the ruling's stop condition)

The ruled rule: repaint when the workspace's rows change, through the same notification the Targets list already uses for its pages and refreshes, no polling timer; if the screen cannot hear that notification within the allowed files, STOP with the options. It cannot, within the allowed files.

How P3 does it: the workspace rebuilds the rail, sidebar and fleet view in `_sync_fleet_widgets`, scheduled by `_mark_fleet_dirty` → `call_after_refresh` (terminal.py). A target-row change (`_targets_reply` after a page/refresh) calls `_mark_fleet_dirty`, which rebuilds those widgets directly. There is no message posted, no per-target callback list a pushed screen can register with, no reactive on `TargetsModel.rows`, and `app.taps` (`app.tapped`) fires only for session record changes (`TAP_RECORD`) and slot closes (`TAP_SLOT_CLOSED`), keyed on `session_id` — never a target-list change; `screen_change_signal` fires only on push/pop. The New session screen is a separate `Screen` pushed on top of the workspace, so nothing in terminal.py reaches it.

To make New session repaint on row change, terminal.py's `_sync_fleet_widgets`/`_mark_fleet_dirty` must call into any open New session screen — a hook in terminal.py, which is not an allowed file. **Options:** (1) add that hook in `terminal.py` (out of the allowed set) so the workspace notifies open New session screens on a row change; (2) a light `set_interval` repaint in `new_session.py` — forbidden by the ruling ("add no polling timer"); (3) leave the selection-by-id fix (finding 1) as the correctness part and record that the painted window is not refreshed between key events. The selection stays on the entity, so the mark tracks the target; only the painted window is stale. I added no repaint timer.

This gates two subparts above: finding 10's "a later successful list selects the saved id" (the id is kept; selecting it when a later list lands needs this notification) and finding 11's "refetches **and repaints**" for `target_not_found`/`inventory_unavailable` (the refetch is in the allowed files; painting the rows as they land needs this notification).

### CI timing failure — `tests/test_no_join_on_loop.py:108` ("the unmount waited 0.50s for the poller")

Ran that file 4 times on this tree (with this phase's fixes): **3 passed** every run, exit 0. Ran it 4 times on `86e11c2`'s code (tsb_tui checked out at `86e11c2`): **3 passed** every run, exit 0. So it does **not** fail on this tree and does **not** fail on `86e11c2`. It is a load flake on the shared CI machine (the assertion is `elapsed < 0.2` for the workspace unmount, racing a held poller reply). This phase's screen does not cause it: the add-target watch (`set_interval(0.1)`) lives in the New session screen, which is not open in this test — the test drives the workspace/poller directly with `fleet_view.refresh_s=1`. No fix needed; not this phase's.

### Gate round 2 (foreground, `env -u NO_COLOR`, interpreter `$(PYTHON)`, each to a file in `.lane-logs/`, exit code checked, never through a pipe)

| run | command | result | exit |
|---|---|---|---|
| new P4 suite (fixed) | `$(PYTHON) -m pytest tests/test_redesign_new_session.py` | **37 passed** (33 + 4 new: findings 9, 10, 11 keys-only + watch) | 0 |
| keys suite | `$(PYTHON) -m pytest tests/test_redesign_keys.py` | **20 passed** | 0 |
| login suite | `$(PYTHON) -m pytest tests/test_new_session_login.py` | **13 passed** | 0 |
| connection-choice suite | `$(PYTHON) -m pytest tests/test_connection_choice.py` | **7 passed** | 0 |
| credential-choice suite | `$(PYTHON) -m pytest tests/test_credential_choice.py` | **14 passed** | 0 |
| no-join-on-loop suite | `$(PYTHON) -m pytest tests/test_no_join_on_loop.py` | **3 passed** | 0 |
| ruff check (no cache) | `PATH=$(VENV_BIN):$PATH ruff check --no-cache tsb_tui tests` | **All checks passed** | 0 |

Red-first on `ef98743` (new_session.py at HEAD, the four new tests added): the 4 finding tests fail (first failing lines above); the 33 phase tests pass.

### Not verified / left

- Finding 12 is STOPped with options (the ruling's stop condition: the screen cannot hear the workspace row-change notification within the allowed files). No repaint timer added.
- Findings 10 and 11 have a repaint subpart gated by finding 12 (finding 10's later-list-selects-id, finding 11's refetch-and-repaint rows landing). The in-allowed-file parts are done: the bound + inventory state + kept id; the focus-to-Create + `r` repeats create / refetches.
- The CI timing failure is a load flake, not this phase's; it passes on this tree and on `86e11c2`.
- The two credential baselines were regenerated with the settled snapshot harness (in `tests/test_redesign_new_session.py`); the credential snapshot cases pass 5 runs in a row.
- `git diff --name-only`: `tsb_tui/screens/new_session.py`, `tests/test_redesign_new_session.py`, `plans/reports/build-261123-redesign-p4-new-session.md`, `tests/snapshots/redesign-p4/credential-80x24.svg`, `tests/snapshots/redesign-p4/credential-160x45.svg` — all allowed. `.lane-logs/` deleted at the end. No commit, no push.

## Qodo round 3 — the ruled repaint hook (finding 12, this turn, 2026-11-23)

The orchestrator ruled finding 12 as **Option 1**: `tsb_tui/screens/terminal.py` is in the phase's allowed list. The smallest hook is added there: when the workspace's target rows change (in `_targets_reply`, at the two points where `_mark_fleet_dirty` is called for a target reply — the background publish and the foreground apply), the workspace calls `_notify_new_session_rows()`, which finds an open `NewSessionScreen` in `self.app.screen_stack` and calls its `notify_target_rows_changed()`. That method repaints the painted window (`_paint_rows()`) and, if the screen holds a saved target id from the add-target watch (`_pending_select`), selects it once the row lands (the same select-on-landing as `_check_pending`, with no tick consumed). No polling timer; no other change to `terminal.py`.

The hook closes finding 12's stop and completes the two gated subparts:
- **finding 10** — a saved target that lands after the 5-second watch ended is now selected (`_pending_select` is kept; the notification selects it on a later landing).
- **finding 11** — `r` refetches for `target_not_found`/`inventory_unavailable`, and the fresh rows repaint as they land (the notification repaints), not only at the press.

### Red first on `ef98743` (terminal.py + new_session.py at HEAD, the three tests added)

| test | first failing line | why it fails on `ef98743` |
|---|---|---|
| `test_rows_that_land_after_open_are_repainted_without_a_key` | `tests/test_redesign_new_session.py:880` — `assert 'Targets · 1/9' in 'Targets · 1/8/8'` | a workspace refresh lands a new row after New session opens; with no row-change hook the painted window stays at the mount-time count, so it never repaints to 9. |
| `test_a_not_found_refetch_repaints_the_rows_as_they_land` | `tests/conftest.py:122` — "timed out after 10s waiting for the refetched rows to land" | on `ef98743` the recovery focus is not moved to Create (finding 11 absent), so `r` types into the filter and no refetch runs; the rows never reach n+1, so nothing repaints. |
| `test_a_saved_target_that_lands_after_the_watch_ended_is_selected` | `tests/conftest.py:122` — "timed out after 8s waiting for the bounded watch to end" | on `ef98743` the add-target watch is unbounded (finding 10 absent), so the watch never ends; a later list that lands the saved row is never selected. |

### Green on the fixed tree

`tests/test_redesign_new_session.py` → **40 passed** (the 37 from round 2 + 3 new). The three ruled behaviours: rows that land after `n` appear without a key press; a refetch after `target_not_found` repaints as the rows land; a saved target that lands after the 5-second watch ended is selected.

### Gate round 3 (foreground, `env -u NO_COLOR`, interpreter `$(PYTHON)`, each to a file in `.lane-logs/`, exit code checked, never through a pipe)

| run | command | result | exit |
|---|---|---|---|
| new P4 suite | `$(PYTHON) -m pytest tests/test_redesign_new_session.py` | **40 passed** | 0 |
| keys suite | `$(PYTHON) -m pytest tests/test_redesign_keys.py` | **20 passed** | 0 |
| login + connection-choice | `$(PYTHON) -m pytest tests/test_new_session_login.py tests/test_connection_choice.py` | **20 passed** | 0 |
| credential-choice suite | `$(PYTHON) -m pytest tests/test_credential_choice.py` | **14 passed** | 0 |
| no-join-on-loop suite | `$(PYTHON) -m pytest tests/test_no_join_on_loop.py` | **3 passed** | 0 |
| ruff check (no cache) | `PATH=$(VENV_BIN):$PATH ruff check --no-cache tsb_tui tests` | **All checks passed** | 0 |

### Left after this turn

- Changes this turn: `tsb_tui/screens/terminal.py` (the `_notify_new_session_rows` hook + its two calls in `_targets_reply`) and `tsb_tui/screens/new_session.py` (`notify_target_rows_changed`), plus the three new tests in `tests/test_redesign_new_session.py` and this report. `git diff --name-only` lists only the prior-round accepted files plus `tsb_tui/screens/terminal.py`. No out-of-scope path touched; no commit, no push.
- The two credential baselines are from round 2 (finding 9), unchanged this turn. `.lane-logs/` and `.t/` deleted at the end.

## Qodo round 4 — Qodo's second review of tsb-tui PR #117 (head `a87357e`, this turn, 2026-11-23)

Qodo named three findings on the last rounds' code, all in `tsb_tui/screens/new_session.py`. All three are **real**. Each is fixed red-first on `a87357e` (first failing line recorded), then green. The plan's rule governs finding 1: after a repaint, Enter acts on the row the mark shows.

### Finding 1 — after a list refresh the mark and Enter point at different targets — REAL, fixed (present case); STOP on the absent-entity fallback

`notify_target_rows_changed` repainted with the stale `_sel` index: when a refresh inserted a row above the highlighted target, the painted mark (`rows[_sel]`) landed on a different row than the entity `Enter`, `Create` and `selected_target_id` act on (`_sel_id`). Fix: before repainting, `_rederive_mark()` finds `_sel_id` in `_matched_rows()` and sets `_sel` to its index, then repaints and calls `_show_type()`. The mark now follows the entity, so Enter acts on the marked row.

**STOP (design gap the recommended fix leaves open).** When `_sel_id` is *not* in the filtered rows after a change — the selected target left the view, or a filter excludes it — the plan rule "selection follows the entity" has no entity to follow, and Qodo's recommended fix offers two fallbacks without choosing. I implemented only the settled present case and left the index as-is for the absent case; I did not pick a fallback:
- **Option A** — call `_follow_selection()`: with no filter, clamp `_sel` and set `_sel_id` to the row now at that index; with a filter, reset to the first match (`_sel=0`, `_sel_id=rows[0].id`). Reuses the existing filter-change convention.
- **Option B** — clamp `_sel` to the last row and set `_sel_id` from that row (no filter reset).
Recommendation: **Option A**, because it reuses the one convention the screen already has for a selection whose row is gone (`_follow_selection`), keeping one rule for "the entity vanished" across filter and refresh. This needs the orchestrator's ruling before it ships; the present-case fix ships now.

Red first on `a87357e`:
- `tests/test_redesign_new_session.py:826` — `assert rows[app.screen._sel].id == app.screen._sel_id` → `('fixture-target', 'normal')`: a refresh inserts `aaa-before` above the prefilled `normal`; with the stale index the mark sits on `fixture-target` while the entity is `normal`.

### Finding 2 — `r` after a connect refusal does not repeat the same create — REAL, fixed

For `target_connect_failed`, `action_refresh_list` called `_create_from_row(self._pending_target_id)`, which re-reads the reuse radio and connection-id input, so the retry sent what the screen showed now, not what was submitted; a typed login (already cleared by `_on_create_error`) fell back to a stored credential. Fix: guard with `_creating`; repeat the submitted target, connection mode, connection id and credential from `self._last_create` through `_create`. When the failed create carried a typed login (`_last_create is None`), do not retry silently: re-show the refusal (`_show_refusal`) and focus the login fields so the person retypes. This follows round 1's ruling (finding 11: the person's `r` repeats the create; nothing retries by itself).

Red first on `a87357e`:
- `tests/test_redesign_new_session.py:855` — `assert len(_creates(served)) == before` → `(2, 1, 'r sent a create without the login')`: after a typed-login connect refusal, `r` sent a second create with the login cleared.

### Finding 3 — the watch's "r retry" message is not wired to the `r` key — REAL, fixed

When the add-target watch ends, `_check_pending` showed `TARGETS_UNAVAILABLE_COPY` ("Could not load targets · r retry") without setting `_recovery_code` or moving focus off the filter, so `r` typed into search; and neither it nor `notify_target_rows_changed` cleared the message when the saved row landed. Fix: at the watch end set `_recovery_code = "inventory_unavailable"` (which `action_refresh_list` handles as a refetch) and focus `#create-button`; track `_watch_copy_shown`, and in `notify_target_rows_changed` and `_check_pending`'s landing branch clear `#new-session-error` only while that flag is set, so an unrelated refusal is never erased. Follows round 1's rulings (finding 10: bounded watch keeps the saved id; finding 11: `r` refetches for `inventory_unavailable`).

Red first on `a87357e`:
- `tests/test_redesign_new_session.py:885` — `assert app.screen.focused.id == "create-button"` → `Input(id='new-session-filter')`: after the watch ends, focus stayed on the filter, so `r` typed into search.
- `tests/test_redesign_new_session.py:992` — `assert "Could not load targets" not in _joined(app)`: the message stayed after a later refresh landed and selected the saved target.

### Green on the fixed tree

`tests/test_redesign_new_session.py` → **42 passed** (the 40 from round 3 + 2 new tests; the two watch tests gained assertions). The four red-first tests pass: the mark follows the entity through a row inserted above it; `r` after a typed-login connect refusal sends nothing and refocuses the login; the watch's message moves focus to Create so `r` refetches; the saved row landing clears the watch message.

### Gate round 4 (foreground, `env -u NO_COLOR`, interpreter `$(PYTHON)`, each to a file in `.lane-logs/`, exit code checked, never through a pipe)

| run | command | result | exit |
|---|---|---|---|
| new P4 suite | `$(PYTHON) -m pytest tests/test_redesign_new_session.py` | **42 passed** | 0 |
| keys suite | `$(PYTHON) -m pytest tests/test_redesign_keys.py` | **20 passed** | 0 |
| login suite | `$(PYTHON) -m pytest tests/test_new_session_login.py` | **13 passed** | 0 |
| connection-choice suite | `$(PYTHON) -m pytest tests/test_connection_choice.py` | **7 passed** | 0 |
| credential-choice suite | `$(PYTHON) -m pytest tests/test_credential_choice.py` | **14 passed** | 0 |
| ruff check (no cache) | `PATH=$(VENV_BIN):$PATH ruff check --no-cache tsb_tui tests` | **All checks passed** | 0 |

### Left after this turn

- Changes this turn: `tsb_tui/screens/new_session.py` (`_rederive_mark`, `_clear_watch_copy`, `_watch_copy_shown`, the typed-login branch in `action_refresh_list`, `_refusal_label`/`_show_refusal`, the watch-end focus + recovery code, and the reuse of `_refusal_label` in `_on_create_error`) and two new tests plus two added assertions in `tests/test_redesign_new_session.py`, plus this report. `git diff --name-only` lists only `tsb_tui/screens/new_session.py` and `tests/test_redesign_new_session.py`. `tsb_tui/screens/terminal.py` was allowed but not changed — the round-3 hook already delivers the row-change notification; finding 1's fix is entirely in `new_session.py`. No out-of-scope path touched; no commit, no push.
- **Open design decision (finding 1, absent-entity fallback):** the two options above; Option A recommended. Needs the orchestrator's ruling before it ships. The present-case fix ships now.
- `.lane-logs/` deleted at the end.
