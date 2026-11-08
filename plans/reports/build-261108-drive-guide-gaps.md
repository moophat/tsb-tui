# Build report — drive guide gaps

**Branch:** docs/drive-guide-gaps | **Model:** strata | **Date:** 2026-11-08

## What was done

### #23 — target_auth_failed and unlisted error codes

Added `target_auth_failed` to GUIDE.md §11 and docs/using-tsb-tui.md §9 with the meaning, next step, and
credential-report guidance from the brief.

Listed every other error code drive can emit that §11 did not list. These are relayed from the core via
`ApiError.code` in `op_new`, `_target_form`, `_new_device_login`:

- `target_connect_failed` — target unreachable or connection failed before session opened
- `no_resolving_credential` — TSB holds no device login for this login on this target
- `target_not_found` — no such target on `new`
- `login_recording_not_acknowledged` — deployment records logins, not acknowledged
- `hostkey_changed` — target's host key changed since TSB recorded it

Each got a row in §11 in the existing style. `target_auth_failed`, `target_connect_failed`, and
`hostkey_changed` also got rows in docs §9. The three already documented in docs §9 (`no_resolving_credential`,
`target_not_found`, `login_recording_not_acknowledged`) needed no docs change.

Updated `test_the_guide_names_every_command_event_and_error_drive_has` to include the relayed codes in its
`built` set, since they come from `ApiError.code` (dynamic) not string literals.

### #26 item 2 — sessions live/own filters

`op_sessions` in `commands.py` gains two optional booleans (`live`, `own`, default false):
- `live` keeps only sessions with no end state (not killed, not terminated)
- `own` keeps only sessions whose owner is this login
- Both may be given; non-boolean values answer `bad_request`

Added the options to §5's `sessions` table.

Tests added in `tests/test_drive_sessions.py`:
- `test_sessions_live_keeps_only_sessions_that_have_not_ended`
- `test_sessions_own_keeps_only_sessions_owned_by_this_login`
- `test_sessions_live_and_own_together_filter_both_ways`
- `test_sessions_non_boolean_live_or_own_answers_bad_request`

### #26 item 1 — tmux recipe

Added a tmux read-only recipe to GUIDE.md §9: attach, read, detach with exact drive commands. The recipe
follows the gate rules: first `type` at the shell prompt (no `full_screen`), `ctrl+b` alone in `keys`
(control key), `d` with `full_screen: true` (text in alt screen).

Test in `tests/test_drive_tmux_recipe.py`:
- `test_tmux_read_only_recipe_passes_the_gate` — runs the recipe's commands through drive against the stub
  server with a full-screen program simulated via `\x1b[?1049h`, checks the gate accepts each step

### #25 — network gear failed-attempt note

Added a bullet to GUIDE.md §9 and docs/using-tsb-tui.md §10: network gear may print a failed-attempt line
(e.g. Junos "Password incorrect.") before a successful login's banner when its authentication order tries
a source that refuses before one that accepts. TSB relays it verbatim; the prompt, not the first line,
says whether you are in.

## Files written

- `tsb_tui/drive/GUIDE.md` — §11 rows (6 codes), §5 sessions table, §9 tmux recipe and #25 note
- `docs/using-tsb-tui.md` — §9 rows (3 codes), §10 #25 note
- `tsb_tui/drive/commands.py` — `op_sessions` live/own booleans
- `tests/test_drive_sessions.py` — 4 new tests
- `tests/test_drive_tmux_recipe.py` — 1 new test
- `tests/test_drive_commands.py` — relayed codes in guide-coverage test

## Test results

`make lint`: 66 passed (import-lint clean, engine-check OK, leak check clean)

Targeted tests (`test_drive_sessions.py`, `test_drive_tmux_recipe.py`, `test_drive_commands.py`):
52 passed in 78s

Full drive suite (`tests/test_drive_*.py`): 262 passed, 1 warning in 258s

## Concerns

None. The relayed codes are dynamic (from the core), not string literals in drive's source; the guide-coverage
test was updated to include them as a known set. If the contract adds more relayed codes in future, that test
set must grow with §11.

DONE
