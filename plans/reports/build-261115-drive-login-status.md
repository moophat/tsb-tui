# Build report — drive login_status command (E4b account rigs)

**AI-Model:** gemini-3.8-flash
**Branch:** feat/drive-login-status (base tsb-tui main `fd0dd22`)
**Date:** 2026-11-15

## Summary

Implemented public drive command `login_status`, ruled by the orchestrator on 2026-11-15 for the E4b account rigs.

1. **`login_status` op**:
   - Takes no fields.
   - Dispatched in `Commands.op_login_status` via `OPS`.
   - Sends `GET /auth/me` with the login drive holds through the published contract client (`Client.me`).
   - Answers `{"status": <integer>}` and nothing else: never the cookie, the principal, or any headers.
   - Returns 200 when live.
   - Returns 401 when the held login ended.
   - Raises `DriveError("not_logged_in", ...)` when drive never logged in or after logout.

2. **Ended login retention until logout or exit**:
   - When a `login_ended` event is emitted (e.g. attachment closed with 4017 due to an account act), `Driver.emit_event` calls `Commands.note_login_ended()`.
   - The ended client is retained in `self._ended_login` exclusively for `login_status` to query the core's answer (401).
   - Every other command continues to check `_screen()` and answers `not_logged_in` as before.
   - Calling `logout` clears `self._ended_login`, returning `login_status` to `not_logged_in`.
   - A subsequent `login` clears any ended login reference upon successful login.

3. **Documentation**:
   - `tsb_tui/drive/GUIDE.md`:
     - Added `login_status` row to Section 5 Commands list table.
     - Documented in Section 14 Account section in the guide's style as a dedicated paragraph.

4. **Tests (`tests/test_drive_login_status.py`)**:
   - `test_login_status_never_logged_in`: verifies `not_logged_in` error on a drive that never logged in and absence of cookies.
   - `test_login_status_held_live_login`: verifies `status: 200` with held live login, absence of cookies, principal, or extra fields.
   - `test_login_status_after_close_4017`: verifies 4017 close emits `login_ended`, `login_status` returns `status: 401`, other commands (`account_list`, `sessions`, `targets`) answer `not_logged_in`, and calling `logout` clears the kept login so `login_status` answers `not_logged_in`.
   - Each new test failed on `fd0dd22` prior to the drive implementation.

## Round 2 — Qodo's two findings on c18418a

Rulings by the orchestrator (2026-11-15) addressing Qodo's two review findings on commit `c18418a`:

1. **Refused retry keeps the ended login**:
   - `op_login` no longer clears `self._ended_login` in its `finally` block; only a successful login (`Driver.log_in`'s clear) or `logout` clears it.
   - Regression test: `test_login_status_refused_retry_keeps_ended_login` in `tests/test_drive_login_status.py`. After `login_ended`, a login attempt with an invalid password is refused, and `login_status` continues to answer 401 using the retained client.

2. **401 discovered by login_status ends the active login**:
   - When `client.me` returns 401 and `client` is still the app's active login (`generation == app._login_gen` and `client is app.client` on an active workspace), `op_login_status` runs the app's standard 401 error path on the UI thread (`self.note_login_ended()` and `app.notify_api_error(exc)`), returning `{"status": 401}`.
   - If the active client was replaced during the in-flight request, no UI state change occurs.
   - Regression test: `test_login_status_active_401_ends_login` in `tests/test_drive_login_status.py`. Invalidation on the server with no attachment event causes `login_status` to discover 401, moving the app to `LoginScreen`, and subsequent session commands answer `not_logged_in`.

3. **Documentation**:
   - Added one sentence in `tsb_tui/drive/GUIDE.md` documenting that a 401 discovered by the probe on an active login ends it like any other 401, returning the app to Login.

## Round 3 — Qodo's finding on a8ff3a8

Ruling by the orchestrator (2026-11-15) addressing Qodo's review finding on commit `a8ff3a8`:

1. **Every 401 ending active login retains ended login under drive**:
   - Under drive, every 401 that ends the active login (from the background poller, probe, or any other source) retains that login's client for `login_status` via `commands.note_login_ended()` in `notify_api_error()` before `_to_login()` removes the active workspace screen.
   - The existing guard protects against a client replaced during the in-flight request (`client is not self.client` drops stale 401s; `isinstance(self.screen, LoginScreen)` and `workspace() is not None` ensure only an active workspace ends).
   - Hook wiring: `Driver.__init__` attaches `app.drive_commands = self.commands`, and `TsbTuiApp.notify_api_error` calls `drive_commands.note_login_ended()` on 401 before tearing down the workspace.

2. **Tests (`tests/test_drive_login_status.py`)**:
   - `test_login_status_poller_401_retains_client`: verifies that when fake core session invalidation occurs with no attachment event and no probe, the poller meets the 401 first, returning the app to Login; `login_status` answers `status: 401` (rather than `not_logged_in`), subsequent session commands answer `not_logged_in`, and calling `logout` clears the retained client. Failed on `a8ff3a8`.
   - `test_login_status_replaced_client_does_not_end_active_login`: verifies that a stale 401 on an old replaced client does not tear down the active workspace or overwrite the live login.

## Round 4 — Qodo's finding on 3e647ae

Ruling by the orchestrator (2026-11-15 21:5x) addressing Qodo's review finding on commit `3e647ae`:

1. **Logout leaves no retained login whatever happens during it**:
   - `note_login_ended` ignores calls while a logout is in progress (`self._logging_out` on `Commands` or `signing_out` on `TsbTuiApp`).
   - `op_logout` sets `self._logging_out = True` around `sign_out()`, and clears the retained login again in a `finally` block after `sign_out` completes or fails on its error path.
   - Any `login_ended` event or 401 callback received while `sign_out()` awaits is ignored and cannot retain an ended client.

2. **Tests (`tests/test_drive_login_status.py`)**:
   - `test_login_status_login_ended_during_sign_out`: fires a `login_ended` event during `sign_out`'s await, verifying that `login_status` answers `not_logged_in` after logout. Fails on `3e647ae` (where `login_status` probed the client retained mid-logout and returned `{"status": 401}`).

