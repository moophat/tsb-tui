# Target create, phase 6 — U7 build report

## Pinned inputs read

`git -C program describe --tags --exact-match`:
```text
v0.14.1
```

`git -C program rev-parse HEAD`:
```text
ad31180f02b48760321070110e2c42a0aafff75a
```

`program/srs/.source`:
```text
# SRS export provenance

exported_at: 2026-11-01T22:33:37+07:00
vault_root:  (node-local; see ops/environment.md)
vault_commit: 51c977da5cc3aa23bda3cce444f384cf2087c00c
vault_state:  clean — this export equals vault_commit
files:        24
exported_by:  tools/export-corpus.sh
```

**Model:** codex
**Branch:** feat/target-form
**Date:** 2026-11-08 (Asia/Saigon)

Read the repository AGENTS.md, README.md, WORK_TRACKER.md and MANIFEST.md; the supplied phase file and requested sections of `tsb-core plans/261029-target-create/plan.md`; the pinned target-write protocol, target operations, TargetDefinition, Me and client methods; and existing form, confirmation and drive patterns. No ledgers or program files were changed.

## Breakdown and checkpoint

- [x] Verify the starting branch, clean tree and pinned inputs.
- [x] Resolve the drive input/output stops using the orchestrator's 2026-11-08 rulings.
- [x] Build the form, permission-gated keys, credential line, confirmed deletion and drive commands.
- [x] Add both named fixture-server test files and adjust affected existing test support.
- [x] Review the pending changes using the ak-code-review specification checklist.
- [x] Run make lint: 66 passed, zero skipped; all other lint gates passed.
- [x] Complete make test: 1739 passed, zero skipped; record the warning below.

## Phase-6 changes finished

1. **Keys.** `LoginScreen` reads `Client.me()` after login; `TsbTuiApp.permissions` stores the names and clears on return to login. `FleetView` handles `n`, `e`, `d` only with `target.edit`. The Targets status hints are clickable; edit/delete click actions are disabled outside source `tsb`. Pressing either key on another source shows the reason in the query/status line. Core write refusals retain the core's message, including a permission revoked after login. Evidence: `test_keys_follow_auth_me_permission` (both grants), `test_other_source_disables_edit_and_delete_with_reason`, `test_hot_policy_refusal_keeps_the_form`.
2. **TargetFormScreen.** Added `tsb_tui/screens/target_form.py` on the existing Textual form pattern, with the eight fields, SSH/Telnet radios, SSH-only account, blank/default or bounded port, comma-split trimmed tags with empty entries refused. Edit reads the published definition and disables Id. Save calls create/replace once, retains the form and every field on refusal, and returns the indexed record on success. The tab refreshes and selects the saved row, paging to it when needed. Saving clears the query so an edited row remains selectable even when it leaves the old query. Evidence: `test_create_edit_and_delete_with_confirmation`, `test_duplicate_keeps_every_field_and_core_message`, `test_invalid_port_or_empty_tag_is_refused_without_write`, `test_saved_target_is_selected_after_it_leaves_the_query`.
3. **The credential section.** A fixed Credential section contains exactly the P2 text, with no credential control: “After you save the target, press l on the Targets tab to save a login for it; otherwise you will be asked at connect.” Evidence: `test_create_edit_and_delete_with_confirmation`.
4. **Delete.** `ConfirmTargetDeleteScreen` names the id, says open sessions stay open, and offers Delete it / Keep it with Escape cancellation. The send half is shared by UI and drive, calls the published delete method and refreshes on success. Evidence: `test_create_edit_and_delete_with_confirmation`, `test_drive_target_new_edit_and_delete`.
5. **Drive.** Added `target_new`, `target_edit`, `target_delete` to OPS and GUIDE.md §5. Create accepts top-level definition fields and returns `target: record`. Its string id is also echoed by the existing correlation envelope. Edit reads the definition, fills the same form, overlays supplied fields, keeps absent fields, clears optional fields on null and refuses null required fields before any request. Telnet drops the account. Delete requires literal true confirmation, sends nothing without it, and returns `target: id` on 204. API refusals answer the core's code/message. Existing `targets` and `new` behavior is retained. Evidence: all tests in `tests/test_drive_target_writes.py`.

Existing test support was updated for the new post-login Me read: the slow HTTP server now serves Me; two login tests count both the login read and the new-session dialog's read. No assertions were removed or weakened.

The first completed full suite exposed a periodic-tick race in `tests/test_sidebar_painted_rows.py::test_the_out_age_clock_that_changes_width_is_painted_by_a_rebuild`: the captured flags were `[False, True]`, because a periodic tick ran during setup before the intended manual tick. The case passed alone. The helper now changes the clock, invokes the explicit tick and snapshots its flags in one app-context call; the exact expected flag assertion and painted-row/rebuild assertions remain. No sidebar production behavior was changed. The complete `tests/test_sidebar_painted_rows.py` file then passed **12 tests**, zero skipped; `make lint` passed again with **66 tests**, zero skipped. The complete full-suite rerun passed after this test-only change.

## Tests and review

New tests in `tests/test_target_form.py`:
- `test_create_edit_and_delete_with_confirmation`
- `test_duplicate_keeps_every_field_and_core_message`
- `test_keys_follow_auth_me_permission` (two cases)
- `test_other_source_disables_edit_and_delete_with_reason`
- `test_invalid_port_or_empty_tag_is_refused_without_write` (five cases)
- `test_hot_policy_refusal_keeps_the_form`
- `test_saved_target_is_selected_after_it_leaves_the_query`

New tests in `tests/test_drive_target_writes.py`:
- `test_drive_target_new_edit_and_delete`
- `test_drive_duplicate_and_forbidden_return_core_errors`
- `test_drive_edit_null_required_field_sends_nothing` (two cases)
- `test_drive_switch_to_telnet_drops_account`
- `test_drive_edit_keeps_empty_optional_values_until_null_clears_them`

The tests use the contract's `serve` fixture (`tsbc._serve.ServeApp`) and the real Textual app/drive runner, never a live broker or sshd.

Final focused run: `tests/test_target_form.py` and `tests/test_drive_target_writes.py` — **18 passed**, zero skipped.

Intermediate focused run: form, drive, attach confirmation and new-session login tests — **35 passed**, zero skipped, before the last two regression cases were added. The existing Targets-tab suite passed **16 tests**, zero skipped.

Final commands use the existing `tsb-test-venv` test environment on PATH. The default system interpreter lacked the declared argon2-cffi and precis-i18n dependencies and first failed collection; nothing was installed. The first full run in the existing environment exposed the slow-server Me route and was interrupted to fix it. Those failures are not claimed as passes.

- `make lint`: **66 passed**, zero skipped; ruff clean, import lint clean, leak check clean, docs guard passed its non-PR no-op, engine and headers verified.
- `make test`: final run **1739 passed, zero skipped, 1 warning** in 1694.31 s (28:14), exit 0. The first completed run was **1 failed, 1738 passed, zero skipped, 1 warning** in 1693.90 s; the failure and repair are described above.

The final full run emitted `RuntimeWarning: coroutine 'TerminalScreen._attach_and_pump' was never awaited`, reported during `tests/test_drive_events.py::test_the_harness_profile_counts_output_and_the_drive_profile_does_not`. The earlier completed run also emitted this warning, reported during a different drive test. Its cause has not been investigated or repaired in this target-form build. It is the reason for DONE_WITH_CONCERNS; neither required command failed and no test was skipped.

Reviewed permission visibility, source gating, immutable id, full-definition replacement, optional-field preservation/clearing, confirmation before sends, retained refusal fields and bounded file ownership. No independent reviewer agent was used.

## Decisions and scope

The two previously reported drive decisions were settled by the orchestrator's 2026-11-08 follow-up; the implementation follows that ruling. No further external drive contract choice remains open.

The orchestrator-owned MANIFEST still names v0.12.1; the exact git tag/hash and `make contract-check` verify v0.14.1. The ledger was left untouched as instructed.

Only repository `tsb_tui/`, `tests/` and this report were changed. The pinned `program/` remains clean at v0.14.1. Ledgers, AGENTS.md, CLAUDE.md, external plans and requirement sources were not edited. No commits, pushes or releases were made. The pin bump was complete on entry; release and tsb-core pin remain the orchestrator's work, excluded by the brief.

DONE_WITH_CONCERNS

## Fix round

**Model:** codex
**Date:** 2026-11-08 (Asia/Saigon)
**Base:** 83ed6b7 on feat/target-form, PR #28.

The two Qodo findings were accepted and ruled by the orchestrator; this round implements only those fixes and their regression tests.

- [x] **A post-login Me answer must not replace the lock.** `LoginScreen` captures the login generation when it requests Me. `_on_me_done` drops an answer for a replaced login or detached login screen. While locked, it queues the completion through `screen_lock.after_unlock`; the callback checks the generation again before applying Me and switching to the workspace.
- [x] **Locked drive target writes must refuse without side effects.** `target_new`, `target_edit` and `target_delete` check the screen lock at entry and raise the existing `not_available` error with `why: "the screen is locked"`, before validation, a target read/write or opening a form.
- [x] Add regression coverage against the contract fixture server: `tests/test_screen_lock.py::test_a_me_answer_held_past_the_login_lock_waits_for_unlock` holds Me until the served lock engages, verifies no workspace exists while locked, then checks both a current login's completion after a real password unlock and a superseded generation's discarded completion. `tests/test_drive_target_writes.py::test_drive_target_writes_while_locked_send_nothing_and_queue_no_form` runs all three commands with the lock screen up, asserting the error, unchanged TSB requests, no queued screens/callbacks and no form after unlock.
- [x] Focused regression command: **2 passed**, zero skipped, in 2.90 s. An initial run had a test-server override signature error, corrected before this passing result; it is not counted as passing evidence.
- [x] `make lint`: **66 passed**, zero skipped, in 7.80 s; ruff, import lint, leak check and engine checks passed (docs guard: non-PR no-op).
- [x] `make test`: **1741 passed**, zero skipped, **1 warning**, in 1683.63 s (28:03), exit 0.

No additional design decision was needed. No ledgers, program files, external plans, commits or pushes were changed by this round.

The full run emitted `PytestUnraisableExceptionWarning` during `tests/test_drive_events.py::test_the_harness_profile_counts_output_and_the_drive_profile_does_not`: an exception was ignored in the existing `TerminalScreen._attach_and_pump` coroutine (`KeyError: '__import__'`). That warning path was not changed or investigated in this two-finding round. Both required commands passed with zero skips; the warning is the reason for DONE_WITH_CONCERNS.

Final review confirmed the captured generation is checked both when Me arrives and when its deferred completion runs, and that each drive guard precedes all target-command requests and form construction. The diff is limited to the two production files, the two regression test files and this report. No further design decision was encountered, and no commit or push was made.

DONE_WITH_CONCERNS
