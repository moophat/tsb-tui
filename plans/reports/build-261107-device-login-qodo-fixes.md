# Device Login — Qodo Fixes (post-acceptance catch-all, safe logging) Build Report

**AI-Model:** strata (opencode)
**Date:** 2026-11-07
**Branch:** fix/device-login-display-only-round
**Base Commit:** a5c8a12
**Report of:** the orchestrator's ruling of 2026-11-07 on Qodo's review of PR #20

## Changes

### 1. `tsb_tui/screens/device_login.py` — the catch-all in `_run` reports by state (ruling 1)

The catch-all `except Exception` is now split by the pane's acceptance/adoption state (ruling 2026-11-07):

- **Before acceptance (`self.connected` False)** — unchanged as built: `finish("The device connection failed.")`,
  a `connect_failed` result to drive, fleet refresh.
- **After acceptance, before adoption (`self.connected and not self.adopted`)** — the existing
  `_connected_without_attachment()` path is taken, so the pane says the session opened and is in the sessions
  list, the sessions list is refreshed, no "Open again" is offered, and drive's result (if any) is
  `outcome: connected` with the pane's plain sentence.
- **After adoption (`self.adopted`)** — the slot keeps its attachment (the `finally` block does nothing when
  adopted, as before). The pane says in plain words through its existing status mechanism
  (`finish(POST_ADOPT, open_again=False)`):
  `POST_ADOPT = "The session opened, but this pane hit an error finishing the login; it is in the sessions list."`
  Drive gets `{"t": "device_login_result", "outcome": "connected", "why": POST_ADOPT}` with no attach fields,
  **only if it has had no result yet**.

The "no result yet" reading of the ruling: the existing `emit` guard is `self._emit is not None and self.pending
is not None`. To make that guard decide, `_complete` sets `self.pending = None` right after it emits drive's
connected result with attach fields; any later `emit` call (the post-adoption branch included) is then dropped by
that same guard. Nothing is created again and "Open again" is never offered in these paths.

### 2. `tsb_tui/screens/device_login.py` — the catch-all logs safely (ruling 2)

New module logger `LOG = logging.getLogger("tsb_tui.device_login")`. The catch-all first logs at WARNING:

```python
LOG.warning("unexpected %s\n%s", type(exc).__name__, "".join(traceback.format_tb(exc.__traceback__)))
```

Only the exception's **type name** and its **stack frames**. Never `str(exc)`, `repr(exc)`, its arguments,
`exc_info=True` or `logger.exception` — a message can carry device text or an answer. Nothing of it reaches the
person (the pane's status/finish texts are the fixed sentences) or drive.

### 3. `tests/test_device_login_pane.py` — two new tests

- `test_an_attachment_failure_after_connected_never_says_connect_failed` — monkeypatches
  `AsyncDeviceLogin.attachment` to raise (`RuntimeError("the session ended before its screen arrived")`: the
  session ending before its screen arrives), drives the pane through the password answer, and asserts: the pane
  is connected and not adopted, no emitted result says `connect_failed`, the last result is `connected`, the
  status says "it is in the sessions list", and "Open again" is not displayed.
- `test_an_unexpected_error_is_logged_by_type_and_frames_never_its_message` — raises
  `RuntimeError(marker)` (marker = `secrets.token_urlsafe(12)`) inside `render_frame`, then asserts a record on
  the logger `tsb_tui.device_login` exists, names `RuntimeError`, and contains no marker text.

No helper was needed in `tests/device_login_support.py`; it is unchanged.

## Red proofs on a5c8a12 (new tests copied onto the base commit's code)

Method: `git stash push tsb_tui/screens/device_login.py` (source change stashed, tests kept), run the new tests
against a5c8a12's code, `git stash pop`.

**Test 1 (attachment failure after acceptance)** — RED on a5c8a12:

```
$ git stash push tsb_tui/screens/device_login.py
$ env -u NO_COLOR PYTHONPATH=. $PY -m pytest tests/test_device_login_pane.py \
    -k "attachment_failure_after_connected" -p no:cacheprovider -q
...
            results = [e for e in emitted if e.get("ev") == "device_login_result"]
>           assert not any(r["outcome"] == "connect_failed" for r in results), (
                f"a result after acceptance reported connect_failed: {results}")
E           AssertionError: a result after acceptance reported connect_failed: [{'ev': 'device_login_result',
                'device_login': 'device-login-1', 'outcome': 'connect_failed', 'error': 'request_failed',
                'why': 'The device connection failed.'}]
1 failed, 42 deselected in 2.36s
$ git stash pop
```

**Test 2 (safe logging)** — RED on a5c8a12:

```
$ git stash push tsb_tui/screens/device_login.py
$ env -u NO_COLOR PYTHONPATH=. $PY -m pytest tests/test_device_login_pane.py \
    -k "logged_by_type_and_frames" -p no:cacheprovider -q
        asyncio.run(check())
        records = [r for r in caplog.records if r.name == "tsb_tui.device_login"]
>       assert records, "the unexpected error left no trace on tsb_tui.device_login"
E       AssertionError: the unexpected error left no trace on tsb_tui.device_login
E       assert []
1 failed, 42 deselected in 2.15s
$ git stash pop
```

(An earlier red run of test 2 had a `NameError` in my own test — `marker` was scoped inside the coroutine; fixed
before the proof above, and both tests were also proven red together: `2 failed, 41 deselected in 4.01s`.)

## Green run (with the fix)

```
$ env -u NO_COLOR PYTHONPATH=. $PY -m pytest \
    tests/test_device_login_pane.py tests/test_drive_device_login.py -p no:cacheprovider -q
.....................................................                    [100%]
53 passed in 75.95s (0:01:15)
```

51 existing + 2 new. The log record the test captured shows exactly what is logged (type + frames, source-line
text only, no marker value):

```
WARNING  tsb_tui.device_login:device_login.py:266 unexpected RuntimeError
  File ".../tsb_tui/screens/device_login.py", line 250, in _run
    await self.render_frame(frame)
  File ".../tests/test_device_login_pane.py", line 233, in broken
    raise RuntimeError(marker)
```

## Checks

| Check | Result |
|---|---|
| Test 1 red on a5c8a12 | PASS (shown above) |
| Test 2 red on a5c8a12 | PASS (shown above) |
| `tests/test_device_login_pane.py` + `tests/test_drive_device_login.py` | 53 passed, 0 failed |
| `ruff check tsb_tui tests` | All checks passed! (`the ruff binary on PATH` is Permission denied in this lane; ran as `$PY -m ruff`) |
| `git status --short` | only `tests/test_device_login_pane.py`, `tsb_tui/screens/device_login.py` (+ this report) |
| Whole suite | not run, as instructed |

## Not verified

- The post-adoption drive path (drive getting `connected` with no attach fields when it had no result yet, and
  getting nothing after `_complete` sent its result) is implemented per the ruling's `emit`-guard reading, but has
  **no test** — the ruling mandated only the two tests above. The mechanism (`self.pending = None` after
  `_complete`'s drive emit) is my reading of "the existing `emit` guard on a pending result decides 'no result
  yet'"; if the orchestrator meant a separate flag, say so and I will change it. *(covered by round 2 below)*
- End-to-end against a real broker/lab: not run (forbidden here; unit tests use the stub server).
- `make lint` (import-lint, engine check, leak check) was not run — only ruff, as the brief specifies.

---

# Round 2 — the orchestrator's amendment (ruling 2026-11-07, veto by line)

## Changes

### 3. `tsb_tui/screens/device_login.py` — the post-adoption branch says it where it is seen

After adoption `_complete` sets `self.display = False`, so `finish(...)` writes where nobody sees it. The branch
is now:

```python
self.terminal.set_login_status(self.slot, POST_ADOPT)
self.emit({"t": "device_login_result", "outcome": "connected", "why": POST_ADOPT})
```

(the slot's status line, the route `_complete` already uses for the Remember outcome after `connected`), inside the
same `if self._live():` guard. The drive emit and `self.pending = None` after `_complete`'s connected emit are
unchanged.

### 4. `tests/test_drive_device_login.py` — the two tests round 1 flagged

- `test_an_error_after_adoption_leaves_the_attachment_and_sends_connected_once` — monkeypatches
  `TerminalScreen.request_device_size` to raise, so `_complete` fails after adoption and before drive's connected
  result. Asserts: exactly one `device_login_result`, `outcome == "connected"`, `why == POST_ADOPT`, no attach
  fields (`session`, `chip`, `front`, `you`, `holder`, `you_may_type`, `size`), no `connect_failed`, the slot keeps
  its attachment (`slot.session_id` set, `slot.pump.state.dump_seen`), and `slot.login_status == POST_ADOPT`.
- `test_an_error_after_the_connected_result_sends_drive_no_second_result` — monkeypatches
  `vault_flow.save_login` to raise (the Remember save, after drive's connected result; the test names the candidate
  since drive never offers Remember). Asserts the first result is `connected` with attach fields, then that drive
  received exactly one result and no `connect_failed`.

## Red proofs on a5c8a12 (source change stashed, tests kept)

**Test (a)** — RED on a5c8a12 (drive got `connect_failed`):

```
$ git stash push tsb_tui/screens/device_login.py
$ env -u NO_COLOR PYTHONPATH=. $PY -m pytest tests/test_drive_device_login.py \
    -k "after_adoption" -p no:cacheprovider -q
                result = await _event(rig, "device_login_result")
                results = rig.events("device_login_result")
                assert len(results) == 1, f"drive got more than one result: {results}"
>               assert result["outcome"] == "connected" and result["why"] == device_login.POST_ADOPT
E               AssertionError: assert ('connect_failed' == 'connected' ...)
1 failed, 11 deselected in 1.79s
$ git stash pop
```

**Test (b)** — RED on a5c8a12 (the last result is `connect_failed`, i.e. a second result arrived after the
connected one):

```
$ git stash push tsb_tui/screens/device_login.py
$ env -u NO_COLOR PYTHONPATH=. $PY -m pytest tests/test_drive_device_login.py \
    -k "no_second_result" -p no:cacheprovider -q
                first = await _event(rig, "device_login_result")
>               assert first["outcome"] == "connected" and "session" in first
E               AssertionError: assert ('connect_failed' == 'connected' ...)
1 failed, 11 deselected in 1.80s
$ git stash pop
```

(An earlier red run of test (a) failed on `len(results) == 1` with `results` snapshotted before the wait; my own
test bug, fixed by reading the events after `_event`, then re-proven as above.)

## Green run (with both rounds)

```
$ env -u NO_COLOR PYTHONPATH=. $PY -m pytest \
    tests/test_device_login_pane.py tests/test_drive_device_login.py -p no:cacheprovider -q
.......................................................                  [100%]
55 passed in 77.96s (0:01:17)
```

51 existing + 4 new (2 from round 1, 2 from round 2).

`ruff check tsb_tui tests` → `All checks passed!` (via `$PY -m ruff`; the binary is
Permission denied in this lane).

`git status --short`: `tests/test_device_login_pane.py`, `tests/test_drive_device_login.py`,
`tsb_tui/screens/device_login.py`, + this report. Whole suite not run, as instructed. Nothing committed or pushed.

---

# Round 3 — the handoff decides the post-adoption branch (ruling 2026-11-07, on Qodo's re-review of 6f946a6)

Qodo's finding: an error after adoption but before `_complete` clears `slot.device_gate` left the adopted session
behind the gate, refusing input, while drive had heard `connected`.

## Change 5 — `tsb_tui/screens/device_login.py`, the post-adoption branch splits on the handoff

```python
if self.slot.device_gate:
    self.terminal._close_slot(self.slot)
    self.app.toast(UNATTACHED, title="connected")
    self.emit({"t": "device_login_result", "outcome": "connected", "why": UNATTACHED})
else:
    self.terminal.set_login_status(self.slot, POST_ADOPT)
    self.emit({"t": "device_login_result", "outcome": "connected", "why": POST_ADOPT})
```

Inside the same `if self._live():` guard, and the same `LOG.warning` before it. Gate cleared: as built in round 2.
Gate not cleared: the pane never half-attaches — close the slot (detach; the session lives on), tell the person
through the app's toast (the declined-gate path's route, since a closed slot shows no status row), and drive gets
the `connected` result with UNATTACHED's words only if it has had none (`emit`'s pending guard). Never
`connect_failed`. **The toast title I used is `"connected"`** — the declined-gate path's style is a one-word
outcome label (`title=result.status`, and the fleet's `title="session"`, `title="saved"`, `title="mapped"`), and
the outcome being told is that the session connected and lives on. Attaching later from the sessions list goes
through the ordinary attach and its recording gate (no code change: that path already exists).

## Change 6 — `tests/test_drive_device_login.py`

`test_an_error_before_the_handoff_closes_the_slot_and_never_leaves_it_behind_the_gate` — monkeypatches
`DeviceLoginPane._unlocked` to raise, so `_complete` fails after adoption and before the gate is cleared. Asserts:
drive got exactly one `device_login_result`, `outcome == "connected"`, `why == UNATTACHED`, no attach fields, no
`connect_failed`; the adopted slot is taken off the screen (`slot not in slots`, `slot.leaving`,
`slot.session_id` set) and no remaining slot is attached behind a set gate; the toast carrying UNATTACHED's words
was shown.

## Red proof on 6f946a6 (only the source change stashed)

```
$ git log --oneline -2   # 6f946a6 is HEAD
$ git stash push tsb_tui/screens/device_login.py
$ env -u NO_COLOR PYTHONPATH=. $PY -m pytest tests/test_drive_device_login.py \
    -k "behind_the_gate" -p no:cacheprovider -q
                result = await _event(rig, "device_login_result")
                ...
>               assert result["outcome"] == "connected" and result["why"] == device_login.UNATTACHED
E               AssertionError: ...
E                 - The device accepted the login and the session opened, but this pane could not attach to it; it is in the sessions list.
E                 + The session opened, but this pane hit an error finishing the login; it is in the sessions list.
1 failed, 12 deselected in 1.66s
$ git stash pop
```

(On 6f946a6 drive's result carried POST_ADOPT's words, so the assertion fails on the `why` first; the slot behind
the gate and the missing toast are what the rest of the test pins down.)

## Green run (rounds 1–3)

```
$ env -u NO_COLOR PYTHONPATH=. $PY -m pytest \
    tests/test_device_login_pane.py tests/test_drive_device_login.py -p no:cacheprovider -q
........................................................                 [100%]
56 passed, 1 warning in 77.86s (0:01:17)
```

51 existing + 5 new. `ruff check tsb_tui tests` → `All checks passed!` (via `$PY -m ruff`; the binary is
Permission denied in this lane). `git status --short`: `tsb_tui/screens/device_login.py`,
`tests/test_drive_device_login.py` (+ this report). Nothing committed or pushed.

## The warning, and what I did not verify

The green run carries one new `RuntimeWarning: coroutine 'TerminalScreen._attach_and_pump' was never awaited`,
reported against an older test by pytest's late garbage-collection check; running the new test alone shows it
comes from the new path: adoption creates the attach worker, and the ruling's `_close_slot` cancels it in the same
UI turn before the coroutine starts. Consequence I could not fix inside the allowed files: when the worker never
started, `_release_slot` closes the canvas but nothing closes the handed-over device-login socket, so that socket
waits for GC. A clean fix is in `tsb_tui/screens/terminal.py` (`adopt_device_attachment` / `_release_slot`), which
this brief does not allow me to touch — it is flagged, not changed. The test passes with the warning; I did not
silence it.

Not verified: the socket's fate on a real core (no lab, per the brief); the whole suite; `make lint`.

Status: DONE_WITH_CONCERNS
