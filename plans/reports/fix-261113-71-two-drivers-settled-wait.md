# Fix report: two-driver settled wait (#71)

**AI-Model:** gpt-6.1-sol

## Cause and evidence

`wait` without `timeout_ms` uses `drive.wait_timeout_ms`, whose documented default is 30,000 ms (`tsb_tui/drive/GUIDE.md`). The test harness bounds each command at 20 seconds, so a genuinely unsettled screen could outlive the harness, but the reproduced failure is a separate race in the settled wake-up.

`Driver._evaluate_one` checked all predicates first and only then read `settled_in()`. If the event loop was delayed between those operations, the quiet deadline could pass during the predicate check. The subsequent `settled_in()` returned zero or less, and no `call_later` handle was installed. With no later model event, `_Pending` had no wake-up and waited until its outer timeout. A deterministic test reproduces this with a controlled probe clock and a 30 ms delayed evaluation; it fails on the original code with a timeout.

The fix reads the quiet interval before checking predicates, so a positive interval is always armed even when the deadline passes during the check. The existing predicate check then runs again from the timer callback and returns the settled result. No default timeout or contract behavior changed.

## Verification

Red run on the original code:

```text
python3 -m pytest tests/test_drive_settle_wakeup.py -p no:cacheprovider -q
1 failed
```

Focused run (regression green; required group not green):

```text
python3 -m pytest tests/test_drive_two_drivers.py tests/test_drive_waits.py tests/test_drive_settle_wakeup.py -p no:cacheprovider -q
1 failed, 52 passed in 41.98s; exit 1
```

Lint:

```text
PYTHON=... PATH=... make lint
All checks passed; exit 0
```

Baseline: both two-driver parameter cases passed in 15.86s (exit 0), under a 40-second process timeout. Each also ran in the two focused groups, for three executions per parameter, below the 30-run limit. The regression passed in both focused groups.

Both focused groups failed only `tests/test_drive_waits.py::test_a_timeout_is_an_ordinary_answer_with_the_last_lines_of_the_screen`: `elapsed_ms` was 149 for a requested 150 ms timeout. `run_wait` truncates the remaining timeout to integer milliseconds and truncates elapsed time; this separate issue is not changed here. First group: 1 failed, 52 passed in 42.79s; exit 1. Lint ran 110 tooling tests successfully in 72.56s, and ruff, import lint, leak and engine checks passed.

The historical CI execution has no timing trace available here: the reproduction proves a sufficient lost-wake-up mechanism, not the exact scheduling of that run. The fixture echoes input; the ping string is not a continuously running process. The timeout still fires; this race does not bypass every bound, but its default 30 seconds exceeds the harness bound of 20 seconds. No busy-loop or CPU-load reproduction was used.

Status: DONE_WITH_CONCERNS
Summary: Proved a settled-wait wake-up race and fixed it by arming the quiet timer before predicate evaluation; added a deterministic red regression test.
Concerns/Blockers: Required focused gate remains red on the separate 149-versus-150 ms timeout assertion. No full suite, commit or push was performed; plans and ledgers were untouched.
