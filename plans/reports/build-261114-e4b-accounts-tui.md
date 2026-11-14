# E4b step 6 — Accounts and own password change

**AI-Model:** gpt-6.1-sol

## Scope and rulings

Branch `feat/e4b-accounts`, based on the released v0.18.0 client. The supplied
phase, scout and rulings were used as read-only inputs. The operator's 10:0x
ruling settled `current_password_file` / `new_password_file` and literal
`confirm: true` on all five mutations. No plan, ledger, ruling, contract source,
client package, broker code or wheel pin was modified. No commit or push.

## Phase checkbox accounting

- [x] “Scout exact current repo files and record them for orchestrator; identify menu and permissions refresh, lost-login, lock, close/reconnect and device-login paths.” Used the supplied scout and inspected its owners. Added `screens/accounts.py` and the account UI/drive tests under the existing package/test directories. Menu permission handling uses `Facts` and the app's `/auth/me` permissions; all account traffic uses the six released public client methods.
- [x] “Write red consumer/UI tests against released fixture server, matching schemas/registered errors directly.” Initial tests ran before implementation. Corrected tests also ran against an isolated archive of the unchanged base with the same pinned fixture server; the temporary archive was removed afterwards.
- [x] “Implement accepted screen/forms/actions through published client: sorted summaries, fixed errors, masked passwords, create result permission display, no-rights guidance, selected account operations per state, own-change entry for non-admin.” Accounts refreshes `/auth/me` on open and on administrator 403; permission removal closes it. Each mutation has a confirmation form; disable names the account and states logins end at once while sessions run on. Create displays inherited permissions or host-policy no-rights guidance.
- [x] “Keep self-disable refusal and admin self-password behavior visible and consistent with core; caller's successful own-change stays signed in.” The list disables self-disable and explains the rule. Drive preserves the core's self-disable refusal. Administrator self-password warns that the caller ends and returns to Login even without an attachment. Own change preserves its caller and attachment while ending the other login.
- [x] “Handle 4017 in attached and headless/device-login paths; no auto-reattach or fabricated terminal-session end. A real 401 returns to Login; 403 password_change_failed remains in current login.” The close table uses `go_login`, no record read and no reattach. The ended view describes an ended login and a surviving session. Device-login 4017 returns to Login without a fabricated result. Drive reports each attached session once before removing the workspace.
- [x] “Test locks and loss-of-login while form/request is pending; do not apply stale account results into a replacement login. Drop password material after submission/cancellation, never persist.” Tests cover a masked/cancelled form, a request completing under lock, a delayed result after replacement login, refusal of locked drive commands, canaries in outputs, screen/form/input state and transcripts, and submitted password-input clearing. Completion under lock uses the existing deferred-pop mechanism. Existing `read_password_file` handles every file input. Inline account password fields are refused and redacted from transcripts.
- [ ] “Give U11 approved automation actions and stable observable outcomes plus consumer test IDs. Release through its repo process; orchestrator separately pins/installs the wheel in core before live proof.” Automation shapes and consumer IDs below are delivered; release/install/deployed-core proof and CI review are orchestrator dependencies and were not attempted in this no-commit/no-push, fixture-only lane.

## Red evidence

All output files are outside the repository in the system temporary directory; names below identify their basenames.
Exit codes were checked directly, without piping a gate command.

- `e4b-accounts-red.log`: exit 1, 25 failed / 35 passed. Missing account action dispatch and commands, permission-aware keys API and 4017 login outcome were exposed. The event test initially also had an invalid list-knob override.
- `e4b-accounts-red-full.log`: exit 1, 28 failed / 35 passed after adding freshness, stale-response and pending device-login checks. Device-login setup initially missed the fixture's host-key step.
- `e4b-accounts-red-event.log`: exit 1 after correcting the list-knob syntax; `login_ended` was not registered on the base.
- `e4b-accounts-red-corrected.log`: exit 1, 35 failed / 36 passed on the isolated unchanged base, with corrected fixture setup and additional refusal/canary/lock checks. Existing model-close tests passed; account behavior remained red. The initial device-login check could pass through a later HTTP 401 without distinguishing a fabricated result; it was strengthened to capture and reject any `device_login_result`.
- `e4b-accounts-red-device-no-result.log`: exit 1 on the unchanged base, specifically at the assertion forbidding a fabricated device-login result. The strengthened consumer passed on the implementation.
- `e4b-accounts-multi-red.log`: exit 1, two failures. A first 4017 could remove the other attachment before its drive event, and successful administrator self-password could be misreported as a changed login. The focused repair run (`e4b-accounts-multi-green.log`) passed all four selected tests, including the delayed replacement-login check.

## Gate and review

- `make contract-check`: exit 0 (`e4b-accounts-contract-check.log`).
- `make lint`: exit 0 (`e4b-accounts-lint-complete.log`): ruff clean, 110 gate self-tests passed, import lint clean (191 files), docs-branch/leak/engine checks completed. No lint step was skipped.
- `make test`: current run pending (`e4b-accounts-test-complete.log`). Initial run exited 2 on interruption with 1 failed / 224 passed (`e4b-accounts-test.log`), identifying the two new menu-only entries missing from the test allowlist. The next run exited 2 on interruption with 215 passed (`e4b-accounts-test-final.log`) after the parallel Menu regression check reproduced legacy direct-login helpers missing `/auth/me` permissions. Updated the affected helpers and extended the click-through coverage to both new entries; the targeted menu-list check passed. Account buttons use two rows to fit the existing 65-column surface. The broader Menu regression run had 52 passes and the one remaining direct-login setup failure; its corrected focused rerun passed (`e4b-accounts-menu-list.log`). Both Python gates use the shared test environment's Python via `PYTHON` in the process environment only.
- Focused first green: 71 passed (`e4b-accounts-focused3.log`). Expanded consumer run: exit 0, 80 passed (`e4b-accounts-consumer.log`).
- Scoped simplification reviewer left code unchanged. It noted duplicate 401 handling in the list loader; removed that redundant path because the generation-checked request helpers already own lost-login handling. Controller reviewed permission, secret, stale-response and close/event paths. No independent live or CI proof claimed.

## Exact drive shapes for U11

The outer `id` is the ordinary correlation id. All examples are top-level JSON commands:

```json
{"id":1,"op":"account_list"}
{"id":2,"op":"account_new","username":"operator","password_file":"starting-password.txt","confirm":true}
{"id":3,"op":"account_disable","username":"operator","confirm":true}
{"id":4,"op":"account_enable","username":"operator","password_file":"enabled-password.txt","confirm":true}
{"id":5,"op":"account_password","username":"operator","password_file":"replacement-password.txt","confirm":true}
{"id":6,"op":"password_change","current_password_file":"current-password.txt","new_password_file":"new-password.txt","confirm":true}
```

List answers `accounts`, each with `username`, `created_at`, `disabled`. Create
answers `account` and `permissions`; an empty permissions list means no rights
yet. The other successful writes answer the normal `id` / `ok` envelope.
Refusals retain the core's registered code and fixed message as `error` / `why`.
Missing confirmation is `bad_request` and sends no account request. All commands
refuse while screen-locked. Inline `password`, `current_password` and
`new_password` are refused. Files use the existing first-line UTF-8 reader and
its regular-file/pipe and 1024-byte rules. No stdin password door was added.

With `login_ended` enabled in `drive.events`, each affected attached session emits:

```json
{"ev":"login_ended","session":"<id>","reason":"account"}
```

It does not emit `ended`, read a session record or remint tickets. Later commands
see the usual logged-out state. A pending device login instead returns to Login
with no `device_login_result`. The GUIDE commands table documents these shapes.

## Consumer test IDs for U11

- C2/account-login-ended-close-4017: `tests/test_drive_accounts.py::test_account_login_ended_close_4017`, `::test_account_end_reports_each_attached_session_once`; `tests/test_model_closes.py::test_account_close_never_reattaches_reads_no_record_and_goes_login`.
- C2/account-device-login-ended-close-4017: `tests/test_drive_accounts.py::test_account_device_login_ended_close_4017`.
- C1/account-password-change-own: `tests/test_drive_accounts.py::test_own_change_preserves_attached_caller_and_ends_other_login`; `tests/test_accounts_screen.py::test_own_change_nonadmin_short_current_preserves_caller`.
- C1/account-password-change-wrong-current-403, -limited-429; unknown-code by status; real 401: `tests/test_accounts_screen.py::test_own_change_errors_by_status_keep_or_end_login` (parameterized).
- C1/account-list, account-create, account-create-existing-permissions: `tests/test_accounts_screen.py::test_account_list_create_permissions_and_no_rights`; `tests/test_drive_accounts.py::test_all_six_account_commands_and_secret_canary`.
- C1/account-disable, account-enable, account-password-set: `tests/test_accounts_screen.py::test_selected_account_actions_confirm_and_follow_state` (parameterized).
- C1/account-disable-self-409, account-enable-already-enabled-409, account-password-unknown-404, account-create-name-exists-409, account-name-reserved-422: `tests/test_drive_accounts.py::test_account_refusals_are_core_codes_and_fixed_text`.
- C1/account-password-set-self: `tests/test_drive_accounts.py::test_admin_self_password_ends_login_without_attachment` and the command variant of `::test_account_end_reports_each_attached_session_once`.
- C1/account-password-never-echoed (consumer portion): `tests/test_drive_accounts.py::test_all_six_account_commands_and_secret_canary`, `::test_inline_account_passwords_never_enter_transcript`; UI submitted/cancelled fields in `tests/test_accounts_screen.py`.
- Menu/permissions: `tests/test_accounts_screen.py::test_menu_and_keys_hide_accounts_without_permission_and_refuse_dispatch`, `::test_permissions_refreshed_on_open_and_after_forbidden`.
- Lock/staleness: `tests/test_accounts_screen.py::test_lock_during_pending_account_request_defers_completion`, `::test_late_account_result_cannot_reach_replacement_login`, `::test_cancel_drops_password_and_lock_covers_form`; `tests/test_drive_accounts.py::test_locked_account_commands_send_nothing`.
- Confirmation/file-only guardrail: `tests/test_drive_accounts.py::test_account_writes_need_confirmation_and_refuse_inline_secrets`.

The existing consumer-MUST registry links both 4017 consumers and own-change
retention to these tests. These are consumer checks against synthetic fixture
state, not claims about core audit, hashing, storage transactions, limiter
configuration or deployed U2 behavior.

Status: DONE_WITH_CONCERNS
Summary: Implementation and focused checks are complete; final gates are in progress on the latest revision. Release, CI and deployed-core proof remain orchestrator dependencies.

## Round 2 — Qodo's four findings (2026-11-14)

Branch `feat/e4b-accounts`, head `a91fec9` kept. The orchestrator ruled each finding as
Qodo recommended. Red first: six new tests, each failing on `a91fec9`; then the code. Only
`tsb_tui/` (the four files) and `tests/` were written, plus this section. No plan, ledger,
ruling, contract source, client package, broker code or wheel pin was touched. No commit, no
push. Gate output went to its own file in the system temporary directory; exit codes checked
directly, never through a pipe.

**1 — A 4017 refusing an attach applies the account-ended outcome.** A close with 4017 met on
the first attach, on the one-shot 4001 retry, or during the backoff's reattach now applies the
same outcome as a 4017 on an established socket: no retry, no session record read, and the app
goes to Login through `CloseOutcome.go_login`. In `tsb_tui/model/attachment.py`: `start()`
catches a first-attach `AttachRefused` whose row has `go_login`, applies the close and fires
`on_login_needed` (other codes re-raise untouched); `_reattach_once` splits `AttachRefused` out
of the `(AttachRefused, ApiError, OSError)` catch and handles a `go_login` row; and the backoff's
final branch calls `on_login_needed` when the applied outcome has `go_login`. Tests:
`tests/test_model_closes.py::test_first_attach_4017_goes_login_without_retry_or_record`,
`::test_4001_retry_that_hits_4017_goes_login`, `::test_reattach_backoff_4017_goes_login` — each
drives the fake client to raise `AttachRefused(4017)` on that path and asserts `login_ended`,
no reattach, no record read, and the login hook fired.

**2 — `drive`'s `account_list` is sorted by username.** `tsb_tui/drive/commands.py` returns
`{"accounts": sorted(result, key=username)}`, as the GUIDE promises and the Accounts screen
sorts, whatever order the client returned. Test:
`tests/test_drive_accounts.py::test_account_list_is_sorted_by_username`, whose fake client
returns an unsorted list and the answer is asserted in username order.

**3 — Transcript redaction covers every `account_*` command.** Under `--record`,
`tsb_tui/drive/runner.py` redacts `password`, `current_password` and `new_password` for every
`account_*` op plus `password_change` (the prefix check added the missing `account_list`),
rejected commands included. Test:
`tests/test_drive_accounts.py::test_rejected_account_list_password_is_redacted`, which records a
refused `account_list` carrying a `password` and finds no trace of it in the transcript.

**4 — A refused act keeps its code.** In `tsb_tui/screens/accounts.py`, after a 403 on a
non-own act, `refresh_permissions(app)` runs inside `try/except (ApiError, HttpError, OSError,
ValueError)` that swallows its own failure, so the original 403 is always what is re-raised.
Test: `tests/test_accounts_screen.py::test_refused_account_act_keeps_its_code_when_refresh_fails`,
where the account act answers 403 and the permissions refresh (`GET /auth/me`) fails; the
screen shows the 403's message, not the refresh's, and the login is kept.

**Red evidence.** `e4b-accounts-red2.log`: exit 1, the six new tests failed on `a91fec9` for the
right reasons (first attach raised the 4017 through; the retry recorded `ticket_refused` and no
login; the backoff recorded `login_ended` with no login hook; `account_list` echoed the client's
unsorted order; the refused `account_list` wrote its password verbatim; the 403 was replaced by
the refresh's error). The finding-4 red run (`e4b-accounts-red4.log`) showed the refresh's
message, not the 403's.

**Green focused run.** `e4b-accounts-green2.log`: exit 0, all six new tests pass.

**Gate (Round 2).** `make lint` exit 0 (`e4b-accounts-lint-r2.log`): ruff clean, gate self-tests,
import lint, docs-branch/leak and engine checks completed; no step skipped. `make test` full run
(`e4b-accounts-test-r2b.log`): exit 0 — 2127 passed, 3 skipped, every account, attach, close and
drive test green. A first full run (`e4b-accounts-test-r2.log`) caught the pre-existing flaky
`tests/test_new_session_login.py::test_the_activity_signal_in_a_counted_window_is_not_what_nothing_reached_the_core_counts`,
whose counted activity window can catch the background `GET /alerts` poll (1 failed, 2126 passed,
3 skipped); it passed on the second run. This flake is not caused by this work: with these changes
stashed on `a91fec9` the same test is flaky too (it passed on one run and failed on three with the
identical `('GET', '/alerts', None)` race). Report it to the orchestrator as a known issue; it was
not fixed here (out of scope).

Status: DONE_WITH_CONCERNS
Summary: All four Qodo findings are fixed with red-first tests (six new, all green); `make lint`
is exit 0 and the full `make test` is green (2127 passed), apart from one pre-existing flaky
alert-window test that also fails on the untouched base. No commit, no push; release/CI/deployed-core
proof stay orchestrator dependencies.

## Round 3 — a 4017 refusing the first attach must not be treated as attached (2026-11-14)

Branch `feat/e4b-accounts`, head `7617c0e` kept. The orchestrator ruled the fix 2026-11-14 19:0x.
Red first, then the code. Only `tsb_tui/` (two files) and `tests/` (two files) were written, plus this
section. No plan, ledger, ruling, contract source, client package, broker code or wheel pin was touched.
No commit, no push. Gate output went to its own file in the system temporary directory; exit codes were
checked directly, never through a pipe.

**The defect (Qodo).** Round 2's `start()` caught a first-attach `AttachRefused(4017)`, applied the
account-ended close, fired `on_login_needed` and **returned normally**, so `_attach_and_pump` carried on
as if attached: `_sync_size`, `_sync_typist`, `_after_attached`, then `_show_end` set `slot.ended` and
parked an end dialog on a workspace `_to_login` is tearing down. And `account_login_ended` marked only
slots with a `client_id` as leaving, so this never-attached slot was not marked.

**The ruled fix.** `start()` re-raises the `AttachRefused` after applying the outcome and firing the hook;
`_attach_and_pump`'s `AttachRefused` handling returns at once for a code whose close outcome has
`go_login`; `account_login_ended` marks every slot of the login as leaving, a slot that never attached
included. The established-socket 4017 path, the one-shot retry and the backoff stay as round 2 made them.

**Code.** In `tsb_tui/model/attachment.py`, `start()` now applies the close and fires `on_login_needed`
for a `go_login` row, then re-raises the refusal (both branches raise). In `tsb_tui/screens/terminal.py`,
the `except AttachRefused` branch in `_attach_and_pump` returns at once when `close_outcome(exc.code).go_login`
(`close_outcome` added to the module's `attachment` import); `account_login_ended` sets `slot.leaving = True`
for every slot and applies the end close only to an attached, not-yet-closed slot.

**Red first.** `tests/test_attach_and_close.py::test_first_attach_4017_goes_to_login_without_an_end_view_on_the_workspace`
drives a first attach refused with 4017 through the slot's own attach path (spy `_sync_size` and
`_after_attached`) and asserts no size/typist sync, no `_after_attached`, no `slot.ended`, no end dialog
(`slot.ended_dialog_shown` false, `_deferred_end_slot` None, no `EndedScreen`), the slot marked leaving, and
Login reached once. On `7617c0e` it fails at the size-sync assertion (`['stub-session-1'] == []`) — the
carry-on. Red run: `e4b-accounts-red3.log`, exit 1.

**Round 2 test adjustment.** `tests/test_model_closes.py::test_first_attach_4017_goes_login_without_retry_or_record`
drives `pump.start()` directly and previously asserted it returns; the ruling makes `start()` re-raise, so the
test now asserts `pytest.raises(AttachRefused)` around `start()` and keeps every outcome assertion (close applied,
login fired once, no retry, no record read).

**Green focused run.** `e4b-accounts-green3.log`: exit 0, the new test passes. Focused set
(`e4b-accounts-focused3.log`): exit 0, 124 passed across attach/close, reattach-login, drive-accounts,
accounts-screen and ended/reattach.

**Gate (Round 3).** `make lint` exit 0 (`e4b-accounts-lint-r3.log`): gate self-tests (110), import lint
clean (191 files), docs-branch/leak and engine checks completed; no step skipped. `ruff check tsb_tui tests
bench tools` is exit 0, "All checks passed" (`e4b-accounts-ruff-r3.log`) — the venv's ruff is not on `PATH`, so
the `make lint` target itself reports `SKIP: ruff` (ruff is optional outside CI); it was run separately to prove
style. Full `make test` (`e4b-accounts-test-r3.log`): 1 failed, 2127 passed, 3 skipped. The one failure is the
pre-existing alert-stream flake `tests/test_alerts_popup.py::test_a_dismissed_alert_resent_after_a_reopen_stays_dismissed`
(a popup dismiss/reopen timing race in the alert stream, unrelated to the attach path); its focused rerun
passed (`e4b-accounts-alertflake.log`, exit 0). No process started by this lane was stopped.

Status: DONE_WITH_CONCERNS
Summary: The first-attach 4017 no longer carries `_attach_and_pump` on as if attached — `start()` re-raises
after applying the outcome, the attach path returns at once for a `go_login` code, and every slot of the login
is marked leaving; red-first test green, `make lint` exit 0 (ruff clean run separately), full `make test` green
apart from one pre-existing alert-stream flake that passes on rerun. No commit, no push.
