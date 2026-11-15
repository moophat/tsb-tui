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
