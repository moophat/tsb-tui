# Fix #70 — a drive `wait` can answer `timeout` 1 ms before its `timeout_ms`

## Change

`tsb_tui/drive/runner.py`, `DriveRunner`/`Driver.run_wait`: the remaining time to the wait's deadline is now rounded
up, not truncated:

```python
remaining_ms = max(0, math.ceil((deadline - loop.time()) * 1000))
```

`_await` arms `loop.call_later(remaining_ms / 1000.0, …)`, so a remaining 149.9 ms now arms a 150 ms timer instead of
a 149 ms one. The reported `elapsed_ms` is unchanged: it is still the measured `probe.now() - started`, never clamped
or adjusted. Nothing else in `tsb_tui/drive/` changes. `import math` added.

Searched `tsb_tui/drive/` for the same truncation (`int((deadline - …) * 1000)`): the only occurrence was the one
changed in `runner.py:235` (now `:236`). The other deadline loops (`_until_screen`, `pass_gate`) compare
`time.monotonic() < deadline` directly and do not truncate.

## Test (red first)

New deterministic test `tests/test_drive_wait_deadline.py`:
`test_the_timer_a_wait_arms_rounds_the_remaining_time_up_to_the_deadline`.

It binds the real `Driver.run_wait`, `_await`, `_evaluate_one` and `_events_since` to a minimal fake driver, feeds the
running loop a clock that reads 149.9 ms left at the moment the remaining time is computed, and replaces
`loop.call_later` with a recorder that schedules the timeout immediately. No wall-clock sleeps and no retries. It
asserts the armed delay is at least 0.1499 s and is exactly the rounded-up 0.150 s.

Red run on the old code (before `math.ceil`):

```
$ python3 -m pytest tests/test_drive_wait_deadline.py -p no:cacheprovider -q
EXIT=1
F                                                                        [100%]
...
E       AssertionError: the timer was armed 149.000 ms early: the remaining time was truncated
E       assert 0.149 >= 0.1499
1 failed in 0.78s
```

## Green run

After the change:

```
$ python3 -m pytest tests/test_drive_waits.py tests/test_drive_wait_deadline.py -p no:cacheprovider -q
EXIT=0
.................................................                        [100%]
49 passed in 20.86s
```

## Lint

```
$ PYTHON=<test-venv>/bin/python3 PATH=<test-venv>/bin:$PATH make lint
EXIT=0
All checks passed!
110 passed in 70.53s (0:01:10)
import-lint: clean (./ 187 files)
docs_branch_guard: no event, not pull_request — no-op
clean: no node addresses, dev hostnames, secret paths, or workstation/mount paths
engine-check: shared object and all headers verified; object matches PIN
```

(One ruff finding in the new test, `PYI034` on `__enter__`, was fixed by annotating it with `Self`; the lint run above
is after that fix.)

## Files touched

- `tsb_tui/drive/runner.py` — `math.ceil` for the remaining time.
- `tests/test_drive_wait_deadline.py` — new deterministic regression test.

Status: DONE
Summary: `run_wait` now rounds the remaining time up to a whole millisecond before arming its timeout, so a wait can no longer answer `timeout` before its `timeout_ms`; a deterministic test fails on the old truncation and passes after the fix, and lint is clean.
Concerns/Blockers: none.
