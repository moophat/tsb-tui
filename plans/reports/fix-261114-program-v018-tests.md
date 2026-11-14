# Fix — two consumer tests after the program pin moves to v0.18.0

**Model:** strata/strata | **Started:** 2026-11-14
**Branch:** `build/program-v0.18.0` (the only prior change is the `program` submodule moved to v0.18.0)

## Cause of each failure

Both follow from the fixture server's v0.17.0/v0.18.0 changes (`program/contract/tsbc/_serve.py`), read from the
release diff and CHANGELOG.

### Test 2 — `tests/test_target_form.py::test_hot_policy_refusal_keeps_the_form`
v0.17.0 added a permission gate in `ServeApp._http`: a principal with **no** permissions is refused before any
session/target route, with `403 forbidden` and the generic message `a session or target permission is required`.
The test revokes every permission (`server._permissions = lambda user: []`), so the POST `/targets` no longer
reaches `_write_target`'s `target.edit` check; it is refused by that gate. The TUI shows the server's own text, so
the form's `#target-error` now reads the generic message.

**Fix chosen:** assert the generic text the server now sends. The test's name (`..._keeps_the_form`) and docstring
("a revoked permission *still displays the core's 403 message*") are about a hot-policy refusal preserving the form,
not about `target.edit` specifically; the subject (the form and its `id` survive the 403) is unchanged. Per the brief,
the second option applies when the test is not about `target.edit` specifically. Changed one assertion to
`a session or target permission is required`; the `id == "revoked"` assertion is kept.

### Test 1 — `tests/test_screen_lock.py::test_a_wrong_password_keeps_the_lock_and_the_login_and_the_right_one_resumes`
v0.18.0 added redaction of password fields in **recorded request bodies**: `ServeApp.http` stores `captured`, where
the `password`/`current_password`/`new_password` keys are replaced with the fixture `PASSWORD` for `/auth/login`,
`/auth/screen-unlock`, `/accounts` and `/auth/password`. `VaultServe.calls()` reads `self.requests`, so
`serve_app.calls("POST", "/auth/screen-unlock")[0]["password"]` is now the redacted `FIXTURE-NOT-A-PASSWORD`, not
the literal `"not the password"` the test typed. The test asserted the exact password sequence and failed at index 0.

The refused request is **not dropped** — it is recorded, with its password redacted. Redacting passwords from recorded
requests is what the v0.17.0 CHANGELOG records ("the runner checks/redacts both own-change password fields"); the
screen-lock protocol (`protocols/screen-lock.md`, C1 screen-unlock fixtures) governs the core's *answer* (a wrong
password is 403, never 401), not the fixture server's request-record. So this is **not** a contract violation and
**not** a STOP: it is a tsb-program test-runner change, and the refused unlock still reaches the core.

The screen-unlock *rewrite* (`_serve.py` lines ~694–697) is not the cause: it fires only when
`users[user]["password_hash"] != _account_hash(PASSWORD)`, which is false for the seeded fixture login, so no rewrite
runs for it. The cause is purely the recording redaction.

**Fix chosen:** fix the test's own recording helper. `RecordingVaultServe` now overrides `http` to capture the raw
screen-unlock bodies in `screen_unlock_bodies` before the server redacts them; the assertion reads that list, so it
still proves both the refused and the accepted unlock reached the core with their exact passwords. Every assertion is
kept (the refusal kept the lock and login, the right password resumed, `unlock_bodies == []`, no `GET /vault/salt`).

## Files changed
- `tests/test_screen_lock.py` — `RecordingVaultServe` gains a raw screen-unlock recorder; the password-sequence
  assertion reads it.
- `tests/test_target_form.py` — one assertion updated to the generic refusal text.

No change under `tsb_tui/`; no change inside `program/`. No absolute machine path written into any file.

## Gate (each to its own file outside the tree, exit code checked, not through a pipe)
- The two tests: 2 passed.
- `make lint` (with the shared test environment's Python): exit 0.
- `make test` (same PYTHON): **2081 passed, 3 skipped** (`TEST_EXIT=0`).

No commit, no push.

Status: DONE
Summary: Both failures were the fixture server's v0.17.0/v0.18.0 changes — a permission gate that answers the generic
403 before `target.edit`, and password redaction in recorded request bodies; test 2 asserts the new generic text (its
subject is the form surviving a hot-policy refusal, not `target.edit`), and test 1 captures raw screen-unlock bodies in
its own helper so it still proves both unlocks reached the core. Full suite green, lint green.
