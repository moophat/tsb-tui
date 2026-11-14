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

## Round 4 — only the newest permission refresh applies (2026-11-14)

Branch `feat/e4b-accounts`, head `8f3d420` kept. The orchestrator ruled the fix 2026-11-14 22:0x, as Qodo
recommended. Red first, then the code. Only `tsb_tui/` (two files) and `tests/` (one file) were written, plus this
section. No plan, ledger, ruling, contract source, client package, broker code or wheel pin was touched. No commit,
no push. Gate output went to its own file in the system temporary directory; exit codes were checked directly, never
through a pipe.

**The defect (Qodo).** `refresh_permissions()` applied every `GET /auth/me` answer to `app.permissions` and closed
the Accounts screen when that answer lacked `account.admin`, without checking whether a newer refresh had already
completed. Two refreshes overlap — the screen opening, and a refused act's 403 — so an older answer lacking
`account.admin` arriving after a newer one that grants it overwrites the current permissions and closes a screen the
newer answer permits.

**The ruled fix.** Each refresh takes a sequence number from a counter kept per app, keyed by login (`_login_gen`):
a sign-in bumps the generation so a new login starts a fresh count, and an answer to a previous login already fails
the existing generation check. An answer applies its permissions, lock settings and any screen close only when its
number is the newest issued for this login; else it is dropped. A close deferred until after a screen unlock rechecks
that it is still the newest before it runs. Nothing else about refresh changes.

**Code.** In `tsb_tui/app.py` `__init__`, added `self._perm_seq: dict[int, int] = {}` beside `_login_gen` — the newest
issued number per login. In `tsb_tui/screens/accounts.py`, `refresh_permissions()` issues `seq = app._perm_seq.get(generation, 0) + 1`
and records it as the newest before the request; after the answer, if `app._perm_seq.get(generation) != seq` it returns
the current effective permission without applying permissions, locks or a close. The deferred `close_accounts()` rechecks
`app._perm_seq.get(generation) == seq` before popping the screens.

**Red first.** `tests/test_accounts_screen.py::test_only_the_newest_permission_refresh_applies`: a fixture server whose
first refresh `/auth/me` (the screen-open answer) is held until the second refresh's `/auth/me` has answered, and whose
held answer has `account.admin` stripped; the second (newer) refresh answers first granting `account.admin`. The test
opens Accounts, triggers the older refresh, then `action_refresh()` for the newer one, settles both workers, and asserts
`app.permissions` keeps `account.admin` and the Accounts screen stays open. On `8f3d420` it fails: the older answer applied
last, so `account.admin` is gone from `app.permissions`. Red run: `e4b-accounts-red4.log`, exit 1.

**Green focused run.** `e4b-accounts-green4.log`: exit 0, the new test passes. The touched and named test files whole
(`e4b-accounts-tests-r4.log`): exit 0, 42 passed across `tests/test_accounts_screen.py` and `tests/test_drive_accounts.py`.

**Gate (Round 4).** `make lint` exit 0 (`e4b-accounts-lint-r4.log`): gate self-tests (110), import lint clean (191
files), docs-branch/leak and engine checks completed; no step skipped. The venv's ruff is not on `PATH`, so the `make
lint` target reports `SKIP: ruff` (ruff is optional outside CI); `ruff check tsb_tui tests` run separately is exit 0,
"All checks passed" (`e4b-accounts-ruff-r4.log`). No process started by this lane was stopped. The orchestrator runs the
full suite.

Status: DONE_WITH_CONCERNS
Summary: A per-login, per-app refresh sequence number makes an answer apply its permissions, locks and screen close only
when it is the newest issued for this login, and a close deferred past an unlock rechecks it is still newest; red-first
test green, `make lint` exit 0 (ruff clean run separately), the touched and named test files whole green (42 passed). No
commit, no push.

## Round 5 — round 4's rule carried to the two error paths (2026-11-14)

Branch `feat/e4b-accounts`, head `bc73ad5` kept. The orchestrator ruled both fixes 2026-11-14 23:0x, as Qodo
recommended. Red first, then the code. Only `tsb_tui/screens/accounts.py` and `tests/` (one file) were written, plus this
section; `app.py`'s round-4 counter needed no change. No plan, ledger, ruling, contract source, client package, broker
code or wheel pin was touched. No commit, no push. Gate output went to its own file in the system temporary directory;
exit codes were checked directly, never through a pipe.

**1 — A stale refresh's 401 does not end the current login.** `refresh_permissions()`'s 401 handler checked the client
and login generation but not the refresh's sequence before `notify_api_error()`, so an older refresh's 401 arriving after
a newer refresh succeeded for the same login still tore down the workspace and sent the user to Login. Fix: the 401 is
notified only when this refresh's number is still the newest issued for this login (the round-4 success guard), so a
stale failure is dropped; the current refresh's 401 is handled exactly as before. In `tsb_tui/screens/accounts.py`, the
`except ApiError` branch adds `and app._perm_seq.get(generation) == seq` to the notify condition. Test:
`tests/test_accounts_screen.py::test_a_stale_refresh_401_does_not_end_the_current_login` — a fixture server whose older
refresh `/auth/me` answers 401 only after the newer refresh's `/auth/me` has succeeded (granting `account.admin`); the
test opens Accounts, triggers the newer refresh, settles both, and asserts the workspace stays, the Accounts screen is
open and `account.admin` is kept. On `bc73ad5` it fails: the older 401 sent the app to Login (`app.workspace()` is
`LoginScreen`).

**2 — A stale list failure does not overwrite the newer result.** `AccountsScreen._refresh()` checked its load number
before replacing the account list but called `say()` for failures without that check, so an earlier load failing after a
later one populated the list wrote its stale error over the newer result. Fix: apply the same load-number (and
attachment) check before writing a failure's message; the latest load's error is still shown. In
`tsb_tui/screens/accounts.py`, `_refresh()`'s `except` writes the failure only `if self.is_attached and load == self._load`.
Test: `tests/test_accounts_screen.py::test_a_stale_list_failure_does_not_overwrite_the_newer_result` — a fixture server
whose earlier load's `GET /accounts` fails (429, marker message) only after the later load's list succeeded; the test
opens Accounts, triggers the later load, settles both, and asserts the account list keeps the later result and the status
line does not show the stale marker. On `bc73ad5` it fails: the marker appears over the populated list.

**Red evidence.** `e4b-accounts-red5.log`: exit 1, both tests failed on `bc73ad5` for the right reasons (the older 401
reached Login; the stale 429 marker was written over the populated list).

**Green focused run.** `e4b-accounts-green5.log`: exit 0, both new tests pass. The touched and named test files whole
(`e4b-accounts-tests-r5.log`): exit 0, 44 passed across `tests/test_accounts_screen.py` and `tests/test_drive_accounts.py`.

**Gate (Round 5).** `make lint` exit 0 (`e4b-accounts-lint-r5.log`): gate self-tests (110), import lint clean (191
files), docs-branch/leak and engine checks completed; no step skipped. The venv's ruff is not on `PATH`, so the `make
lint` target reports `SKIP: ruff` (ruff is optional outside CI); `ruff check tsb_tui tests` run separately is exit 0,
"All checks passed" (`e4b-accounts-ruff-r5.log`). No process started by this lane was stopped. The orchestrator runs the
full suite.

Status: DONE_WITH_CONCERNS
Summary: Round 4's newest-refresh rule now covers both error paths — a stale refresh's 401 is dropped before
`notify_api_error`, and a stale load's failure message is written only when it is the current load; red-first tests fail
on `bc73ad5` and pass after the fix, `make lint` exit 0 (ruff clean run separately), the touched and named test files
whole green (44 passed). No commit, no push.

## Round 6 — a pending submission cannot be cancelled away, and an own password change says so (2026-11-15)

Branch `feat/e4b-accounts`, head `0454033` kept. The orchestrator ruled both fixes 2026-11-15 00:3x, as Qodo
recommended. Red first, then the code. Only `tsb_tui/screens/accounts.py`, `tsb_tui/screens/terminal.py` and
`tests/test_accounts_screen.py` were written, plus this section. No plan, ledger, ruling, contract source, client
package, broker code or wheel pin was touched. No commit, no push. Gate output went to its own file in the system
temporary directory; exit codes were checked directly, never through a pipe.

**1 — Cancel and Escape cannot dismiss a form whose request is in flight.** `AccountFormScreen.action_back()`
dismissed the modal whenever it was the current screen, and the Cancel button stayed enabled, so while `busy` a person
could close the form; its answer then settled into a dismiss that no longer showed the result, and the Accounts list did
not refresh. Fix: `action_back()` returns at once while `busy` (so both Escape and Cancel are inert), `submit()` disables
the Cancel button with Confirm, and the `finally` re-enables both. In `tsb_tui/screens/accounts.py`. Test:
`tests/test_accounts_screen.py::test_cancel_and_escape_do_not_dismiss_a_pending_submission` — a fixture server holds the
create's `POST /accounts` answer; the test presses Confirm, waits for the request to be pending, asserts the form is
busy and Cancel disabled, presses Escape and Cancel and asserts the form stays, then releases the answer and asserts the
form dismisses with its result (the Accounts list gains the new username and says `Created`). On `0454033` it fails: the
Cancel button is not disabled (and Escape would dismiss the form).

**2 — A successful own password change notifies, a cancel stays silent.** `do_password_change()` pushed
`AccountFormScreen("own")` with no result callback, so a successful change returned to the workspace with no word about
the other logins it ended. Fix: it pushes with a callback that calls `app.notify("Password changed. Your other logins
ended; this one stays.", markup=False)` for a non-None result; a cancel's `None` notifies nothing. In
`tsb_tui/screens/terminal.py`. Test:
`tests/test_accounts_screen.py::test_own_change_notice_on_success_and_silent_cancel` — cancel first and assert no such
notice, then submit a successful own change with the fixture's current password and assert the exact notice arrives. On
`0454033` it fails: no notice is ever raised.

**Red evidence.** `e4b-accounts-red6.log`: exit 1, both tests failed on `0454033` for the right reasons (the Cancel
button was enabled while busy; the own-change notice never appeared).

**Green focused run.** `e4b-accounts-green6.log`: exit 0, both new tests pass.

**Gate (Round 6).** `make lint PYTHON=<test venv>` exit 0 (`e4b-accounts-lint-r6.log`): ruff "All checks passed", 110
gate self-tests passed, import lint clean (191 files), docs-branch/leak and engine checks completed; no step skipped.
`env -u NO_COLOR pytest -q tests/test_accounts_screen.py tests/test_drive_accounts.py` exit 0
(`e4b-accounts-tests-r6.log`): 46 passed. Both gate commands used the shared test environment's Python through the
process environment only; no interpreter path was written into a file. No process started by this lane was stopped. The
orchestrator runs the full suite.

Status: DONE_WITH_CONCERNS
Summary: A pending account submission now ignores Escape and Cancel until its request settles, then dismisses with its
result and refreshes the list, and a successful own password change raises the ruled notice while a cancel stays silent;
red-first tests fail on `0454033` and pass after the fix, `make lint` exit 0 and the two touched test files green (46
passed). No commit, no push; full-suite and release proof stay with the orchestrator.

## Round 7 — Qodo's two findings on 5141f51 (2026-11-15)

Branch `feat/e4b-accounts`, head `5141f51` kept. The orchestrator ruled both fixes 2026-11-15 02:0x, as Qodo
recommended. Red first, then the code. Only `tsb_tui/drive/commands.py` and `tests/test_drive_accounts.py` were
written, plus this section. No plan, ledger, ruling, contract source, client package, broker code or wheel pin
was touched. No commit, no push. Gate output went to its own file in the system temporary directory; exit codes
were checked directly, never through a pipe.

**1 — The op list follows the guide's order.** In `tsb_tui/drive/commands.py`, `OPS` put the six account ops ahead of
`login`, while its comment states "Every op, in the order the guide lists them". Fix: moved the six account ops
(`account_list`, `account_new`, `account_disable`, `account_enable`, `account_password`, `password_change`) to after
`targets` and before `target_new`, exactly matching the order in the `tsb_tui/drive/GUIDE.md` command table, and
aligned `quit` at the end after `login_cancel`. Test: `tests/test_drive_accounts.py::test_ops_order_equals_guide_table`
parses every command op from the GUIDE table in `tsb_tui/drive/GUIDE.md` and asserts `commands.OPS == tuple(ops_in_guide)`.
On `5141f51` it fails at index 0 (`account_list != login`).

**2 — A password-file error names the field that was read.** `read_password_file` previously always hardcoded
`password_file` in its error messages; `_account` reads `current_password_file` and `new_password_file` through it.
Fix: added an optional `field: str = "password_file"` parameter to `read_password_file`, used `{field}` in every error
raised by `read_password_file`, and passed the actual field name from `_account` (`"new_password_file"` when
`operation == "own"`, `"current_password_file"` for the current password, and `"password_file"` otherwise).
Test: `tests/test_drive_accounts.py::test_password_change_bad_password_file_names_field` asserts that a
`password_change` with a bad `current_password_file` gets a `bad_request` error naming `current_password_file`, and one
with a bad `new_password_file` names `new_password_file`. On `5141f51` it fails because the error named `password_file`.

**Red evidence.** `e4b-accounts-red7.log`: exit 1, both new tests failed on `5141f51` for the right reasons
(`account_list` instead of `login` in `OPS`; `password_file` instead of `current_password_file`).

**Green focused run.** `e4b-accounts-green7.log`: exit 0, both new tests pass.

**Gate (Round 7).**
- New tests: exit 0, 2 passed (`e4b-accounts-green7.log`).
- `env -u NO_COLOR python3 -m pytest tests/test_drive_accounts.py tests/test_drive*.py -p no:cacheprovider`: exit 0, 471 passed (`e4b-accounts-drive-tests-r7.log`).
- `make lint PYTHON=python3`: exit 0 (`e4b-accounts-lint-r7.log`): gate self-tests (110 passed), import lint clean (191 files), docs-branch/leak and engine checks completed; no step skipped.
All gate commands used the shared test environment's Python through the process environment only; no interpreter path was written into a file. No process started by this lane was stopped.

Status: DONE
Summary: `OPS` in commands.py now matches the GUIDE table's command order, and `read_password_file` parameterizes the field name so `current_password_file` and `new_password_file` errors name the field that was read; red-first tests fail on 5141f51 and pass after the fixes, full drive suite (471 tests) and `make lint` green.

## Round 8 — a refresh never moves an account action to another account (2026-11-15)

Branch `feat/e4b-accounts`, head `c1b9263` kept. The orchestrator ruled the fix 2026-11-15 03:1x, as Qodo
recommended. Red first, then the code. Only `tsb_tui/screens/accounts.py` and `tests/test_accounts_screen.py` were
written, plus this section. No plan, ledger, ruling, contract source, client package, broker code or wheel pin
was touched. No commit, no push. Gate output went to its own file in the system temporary directory; exit codes
were checked directly, never through a pipe.

**The defect (Qodo).** `AccountsScreen._refresh()` replaced sorted rows and cleared the table without keeping
the selection, and `selected()` named the target by cursor position only (`self.accounts[row]` where `row` was
`cursor_row`). After a refresh that changed the list ordering, Disable, Enable or Set password could open a
confirmation for a different account, or remain enabled for row 0 if the selected account was deleted.

**The ruled fix.** `_refresh()` saves the selected username before rows are replaced (`target = self.selected()`),
suppresses highlight updates during table rebuild (`self._rebuilding = True`), sorts and rebuilds rows, and puts the
cursor back on that username (`table.move_cursor(row=index)`) after the table is rebuilt. If that username is gone,
it clears the selection (`self._selected = None`) and disables the actions that need a target (`disable`, `enable`,
`password`). `self.call_after_refresh` clears the rebuild flag once queued table events settle. `selected()` returns
the username of the selected row (`str | None`), never a position. When a row is chosen via cursor movement
(`on_data_table_row_highlighted`) or row selection (`on_data_table_row_selected`), `self._selected` updates to that
row's username and `_buttons()` re-enables allowed actions. `on_button_pressed` uses the username string directly
and refuses target-dependent actions when `user is None`.

**Red evidence.** `e4b-accounts-red8.log`: exit 1, both new tests failed on `c1b9263` for the right reasons (test (a)
failed because `selected()` returned a dict and cursor did not follow the selected user after insertion; test (b)
failed because `selected()` returned row 0's dict instead of None, keeping target actions enabled).

**Green focused run.** `e4b-accounts-green8.log`: exit 0, both new tests pass.

**Gate (Round 8).**
- New tests: exit 0, 2 passed (`e4b-accounts-green8.log`).
- `env -u NO_COLOR python3 -m pytest tests/test_accounts_screen.py -p no:cacheprovider`: exit 0, 22 passed (`e4b-accounts-tests-r8.log`).
- `make lint PYTHON=python3`: exit 0 (`e4b-accounts-lint-r8.log`): gate self-tests (110 passed), import lint clean (191 files), docs-branch/leak and engine checks completed; no step skipped.
All gate commands used the shared test environment's Python through the process environment only; no interpreter path was written into a file. No process started by this lane was stopped.

Status: DONE
Summary: `AccountsScreen._refresh()` now preserves the selected username across rebuilds and restores the cursor, or clears selection and disables target-dependent actions when that user is gone; red-first tests fail on `c1b9263` and pass after the fix, full screen suite (22 passed) and `make lint` green.

## Round 8b — the selection follows the cursor, restored by name (2026-11-15)

Ruled 2026-11-15 03:4x (the orchestrator): a data table always shows a cursor row, so the selection is the username under the cursor, never a separate state that can disagree with the screen. `selected()` returns the username of the cursor row (or None when the table is empty). `_refresh()` saves that username before replacing rows and restores cursor to that username after rebuild; if that username is not in the new list, cursor goes to the first row. Removed `_selected`, `_rebuilding`, and `call_after_refresh`. Replaced test (b) with: after a refresh where the selected username is no longer listed, the cursor is on the first row and Disable's confirmation names that row's user, never the vanished one.

**Red evidence.** `e4b-accounts-red8b.log`: exit 1, both new tests failed on `c1b9263` for the right reasons (selected returned a dict instead of string; cursor did not restore after rebuild).

**Gate (Round 8b).**
- Five consecutive test runs: `env -u NO_COLOR python3 -m pytest tests/test_accounts_screen.py tests/test_drive_accounts.py -p no:cacheprovider`: exit 0 across all five runs (50 passed each run; `e4b-accounts-gate-run1.log` through `e4b-accounts-gate-run5.log`).
- `make lint PYTHON=python3`: exit 0 (`e4b-accounts-lint-r8b.log`): gate self-tests (110 passed), import lint clean (191 files), docs-branch/leak and engine checks completed; no step skipped.
All gate commands used the shared test environment's Python through the process environment only; no interpreter path was written into a file. All processes started were stopped.

Status: DONE
Summary: Selection follows cursor directly, restored by username across refresh with fallback to row 0; red tests fail on c1b9263, five consecutive test runs (50 passed each) and make lint green.


**Round 9 — complete (AI-Model: gpt-6.1-sol).** Both ruled fixes implemented; test event key corrected to `ev`, displayed status checked instead of the internal connected-frame flag, and notification delivery awaited. Code-only stash on 2a00b95: `77-r9-red.log`, exit 1, 3 failed (4017, 4015, non-admin refusal), 2 passed (transport preservation and open-Accounts notice). Restored code: `77-r9-green.log`, exit 0, 5 passed. The two preservation checks intentionally already pass on 2a00b95; making them red would grade an unruled change. Gate: `env -u NO_COLOR python -m pytest tests -p no:cacheprovider -q`, exit 0, 2141 passed and 3 skipped in 2352.46s (`77-r9-pytest.log`); the skipped installer checks require the CI image's hash-pinned PowerShell/Pester inputs and were not verified here. `make lint PYTHON=python`, exit 0, ruff clean, 110 gate self-tests passed, import lint clean (191 files), docs-branch, leak and engine checks passed (`77-r9-lint.log`). Both commands used the shared test venv interpreter; each output was captured separately outside the tree without a pipe. An accidentally early full-suite run was interrupted (exit 2) before the completed gate run; the initial lint attempt found import ordering, corrected before the successful run. All started test/gate processes finished or were stopped; no unrelated process was stopped. No commit or push; HEAD remains 2a00b95.

**Round 10 — complete (AI-Model: gemini-3.8-flash).** Ruled fix implemented: `AccountFormScreen` built with `"" if operation == "new" else user`, and `compose()` puts a username in the title only when operation is not `"new"`. Test `tests/test_accounts_screen.py::test_create_dialog_not_titled_with_selected_account_and_disable_names_it`: with a row selected, Create's title is exactly "Create account" and its Username box is empty; Disable's title still names the selected account. Proven red on ffeb0d3: exit 1, AssertionError ('Create account: subject' != 'Create account') captured in `77-r10-red.log`. Restored code: `77-r10-green.log`, exit 0, 1 passed. Gate: `env -u NO_COLOR python3 -m pytest tests/test_accounts_screen.py tests/test_drive_accounts.py -p no:cacheprovider -q`, exit 0, 52 passed in 63.95s (`77-r10-pytest.log`); `make lint`, exit 0, 110 gate self-tests passed, import lint clean (191 files), docs-branch, leak and engine checks passed (`77-r10-lint.log`). Both commands captured output outside the tree without a pipe and checked exit codes. All started processes finished; no unrelated process was stopped. No commit or push; HEAD remains ffeb0d3.

