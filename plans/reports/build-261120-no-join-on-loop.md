# 261120 — no background thread is joined on the event loop

**Outcome:** `FleetPoller.stop()` and `AlertStreamer.stop()` return at once; a reply or event still in
`call_from_thread` is dropped; a separate `join(timeout)` waits off the loop. New keys-only and timing tests are red
on main (5.10 s, 1.50 s, 5.09 s) and green after the change.

## The change

- `tsb_tui/model/fleet.py`
  - `FleetPoller.stop()` sets the stop flag and returns; it no longer joins its worker (was `join(timeout=5.0)`).
  - Added `FleetPoller.join(timeout=None)` for callers with no running loop; never called on the loop.
  - `running` is false once the stop flag is set (the daemon may still be finishing its step).
- `tsb_tui/model/alerts.py`
  - `AlertStreamer.stop()` sets the flag and closes the stream, then returns; it no longer joins its worker (was
    `join(min(read_timeout, 2.0) + 0.5)`).
  - Added `AlertStreamer.join(timeout=None)`, same rule as above.
  - `running` is false once the stop flag is set.
- `tsb_tui/app.py`
  - The alert callbacks are bound to their own streamer. `_alerts_from_thread(streamer)` returns without calling
    `call_from_thread` when that streamer is no longer the app's current one.
  - The loop-side `_apply_alerts(streamer)` re-checks the identity, so an event held while `stop_alerts()` ran is
    dropped instead of notifying listeners.
- `tsb_tui/screens/terminal.py`
  - `_poll_reply` returns before `call_from_thread` once `_unmounted` is set; `_apply_poll` keeps its existing
    `_unmounted` guard for a reply already in flight.
- `AttachmentPump` untouched, as ruled.

No join was kept anywhere; nothing else changed.

## Tests — `tests/test_no_join_on_loop.py`

Each test holds the first poll reply / alert event where `call_from_thread` would wait for the loop, then stops the
worker or unmounts the workspace and measures the loop's return. `_track_unmount` records that
`TerminalScreen.on_unmount` has returned, so the measurement includes any join inside it.

| Test | Red on main | After the change |
|---|---|---|
| `test_a_held_poll_reply_does_not_hold_the_unmount` | `tests/test_no_join_on_loop.py:108` — `assert elapsed < 0.2`; measured **5.10 s** (the 5 s join) | passed |
| `test_a_held_alert_event_does_not_hold_the_stop` | `tests/test_no_join_on_loop.py:147` — `assert elapsed < 0.2`; measured **1.50 s** (the 1.5 s join) | passed |
| `test_an_account_ended_login_answers_within_a_second_with_a_poll_in_flight` | `tests/test_no_join_on_loop.py:193` — `assert elapsed < 1.0`; measured **5.09 s** | passed |

After passing the timing line, each test also proves the next drive command (`login_status`) is answered within 1 s,
and that the held reply/event changes nothing (fleet generation unchanged; no alert listener called). The account-act
test drives close 4017 as the existing login-status tests do (`set_account_password`).

Red run: `3 failed in 16.59s` (exit 1, expected).
Green run: `3 passed in 5.09s` (exit 0).

## Gates

Interpreter: the shared test venv's `python` (`python -m pytest`), `NO_COLOR` unset,
`PYTHONPATH=program/contract/client:.`.

| Command | Result | Exit |
|---|---|---|
| `python -m pytest -q -p no:cacheprovider tests/test_no_join_on_loop.py` | 3 passed | 0 |
| `python -m pytest -q -p no:cacheprovider tests/test_drive_login_status.py tests/test_sign_out.py tests/test_attach_and_close.py tests/test_fleet_view.py tests/test_model_alerts.py tests/test_sidebar.py` | 122 passed in 130.21 s | 0 |
| `make lint` | ruff: all checks passed; import-lint clean; docs guard no-op; leak check clean; engine check OK | 0 |
| `make contract-check` | `program/` clean at the pinned release | 0 |

Extra verification:

- `tests/test_alerts_popup.py tests/test_drive_accounts.py tests/test_one_login_screen.py tests/test_view_and_quit.py
  tests/test_model_fleet.py tests/test_no_join_on_loop.py`: 91 passed (exit 0).
- Whole suite by collected file list, split in two runs to stay inside the lane's command limit: part A
  `1224 passed, 3 skipped` (exit 0); part B `1141 passed` (exit 0). The 3 skips are the pre-existing installer tests
  that require the CI image. Total collected: 2368.

## Not verified / notes

- A single-process run of the whole suite was attempted first but exceeded the lane's command timeout; the split runs
  above cover the same collected files. One first-pass run showed one `F` near 27% that did not reproduce in a second
  full pass or in the split runs (the region, `tests/test_drive_screen_lock.py`, passes 45/45 alone), so it appears to
  be an existing flaky timing test, not a change in this work.
- No real broker, lab, core or sshd was used; test doubles and the contract fixture server only.
- `running` now reports false as soon as a stop is asked for, which is what keeps the existing stop tests meaningful
  without a join. `join(timeout)` is the only waiting path and is not called from the loop.
