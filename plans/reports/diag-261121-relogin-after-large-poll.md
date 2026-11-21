# Reproduction — `login` after `logout` with a large session list loading (v0.11.0)

Lane: tests only, no product change. The diagnosis's mechanism was unproven; this writes the
reproducing test the diagnosis named, instruments it to separate its two candidates, runs it 10 times
standalone, and stops with evidence. The fix is the orchestrator's call.

## The test — `tests/test_relogin_after_large_poll.py`

Pilot + drive against the contract's in-process fixture server (never a broker or sshd), patterned on
`tests/test_no_join_on_loop.py`:

- A test double for the logged-in client's `sessions()` returns ~28,000 valid session records. The
  first call is held on a `threading.Event` gate so the reply lands exactly where the test puts it;
  later calls sleep. `fleet_view.refresh_s` is set high so the workspace issues one controlled poll.
- Sequence per run: drive `login` (the workspace mounts, its first poll blocks on the gate), then
  `logout`, then `login`.
- Timing is varied across three cases so a race is caught: the reply released **before**, **during**
  and **after** `logout`.
- Asserts: `login` answers `ok` within drive's 30 s wait; the fixture server recorded
  `POST /auth/login` before the answer; a late poll reply changed nothing (the old workspace's
  `fleet.applied_generation` unchanged after logout).

## Instrumentation (test-side only — wrappers, spies, monkeypatches)

For a failing run the test records:
- `LoginScreen._attempt_login` — how many times it ran, plus `app.focused` and `app.screen` at each call.
- `app.call_api` — every request it submitted (by qualname).
- `Client.login` — whether the worker reached the executor and on which thread it ran.
- The pilot's `press` — `app.focused` and `app.screen` at the moment drive pressed enter.
- At the timeout: `#login-button.disabled` and `#login-error.content`.

This separates candidate (a) "enter dispatched to a screen with no focused widget" from candidate (b)
"submit ran, button disabled, worker never queued".

## The timings tried

Three reply-release points relative to `logout`: **before** (reply applies fully), **during** (released
as `logout` runs), **after** (released once `logout` has answered). Each point is exercised in every
run.

## The 10 runs (each its own process; `pytest -q -p no:cacheprovider`)

Every run: 3 passed (the three timing cases). Wall time per run ~4.2–4.6 s. No case failed, so the
instrumented failing-run evidence was never produced.

```
run 1  : 3 passed in 4.53s
run 2  : 3 passed in 4.54s
run 3  : 3 passed in 4.55s
run 4  : 3 passed in 4.45s
run 5  : 3 passed in 4.59s
run 6  : 3 passed in 4.51s
run 7  : 3 passed in 4.68s
run 8  : 3 passed in 4.25s
run 9  : 3 passed in 4.25s
run 10 : 3 passed in 4.23s
```

## Evidence that the scenario is exercised (one instrumented run, `-s`)

The test drives the real path; the values below show the re-login dispatches correctly in all three
timings, so the stall did not reproduce in-process:

- **before**: big reply applied (`applied_generation=1`), old workspace `unmounted=True`,
  `login_posts=2` (the initial login + the re-login).
- **during**: reply released mid-`logout`; late reply dropped, `unmounted=True`, `login_posts=2`.
- **after**: reply released after `logout` answered; dropped (`applied_generation=0`),
  `unmounted=True`, `login_posts=2`.

In every case, at the enter press: `app.focused = Input(id='password')` and `app.screen = LoginScreen`
(focus landed on the password field, so candidate (a) is not in play here); `_attempt_login` ran;
`call_api` submitted `login`; `Client.login` ran on an executor thread; `POST /auth/login` reached the
server. So neither (a) nor (b) fired under these timings.

## The failing link

None found. The test never failed across 10 runs and three reply-release timings, so there is no
failing link to name with evidence.

## Fix options

The reproduction did not go red, so no fix is warranted from this run. The diagnosis's options
(§3 of the write-up) stand unchanged; the orchestrator rules. The test is in place to re-run against
any candidate fix.

## Not verified

- The double returns 28,000 dicts directly; it does **not** run the contract's schema-validated
  `loads()` of a 12 MB body. The diagnosis's leading candidate ((a), the heavy validated reply
  entangling the poller's `call_from_thread` handoff with loop callbacks around the screen swap, leaving
  the freshly pushed LoginScreen's focus unset) depends on that CPU/loop pressure; this test does not
  reproduce it, so a red run for candidate (a) is not ruled out — only not seen here.
- No end-to-end run against a broker, sshd, the lab or staging (per the brief: tests use the fixture
  server and doubles only). The stall dump and proxy log were read as given, not re-created.
- `make lint` ran with ruff not installed on this box, so the Python style check was skipped by the
  Makefile's own fallback (exit 0; import-lint, leak-check, docs-guard, engine-check all passed).

Status: DONE_WITH_CONCERNS
Summary: The reproducing test drives drive `login` after `logout` with a ~28,000-record poll reply
released before, during and after `logout`, instrumented to separate the diagnosis's two candidates;
it never failed in 10 standalone runs (focus landed on `#password`, `_attempt_login` ran, the worker
reached the executor, `POST /auth/login` reached the server), so there is no failing link — the
schema-validated 12 MB load that candidate (a) needs is not modelled by the double, so a red run for
that path is not ruled out.

---

# Round 2 — the same reproduction through the real load path

Round 1's double returned dicts and skipped the contract client's parse of a 12 MB body. Round 2 puts
~28,000 session records in the served store so the workspace's poll is the **real** `Client.sessions()`
over HTTP with the contract client's own decode, `refresh_s` as the app ships it (5 s), and runs the rig's
script (`new`, type, `end` confirm, wait for the end, `logout`, `login`). Added to
`tests/test_relogin_after_large_poll.py` as `test_relogin_after_real_large_load`.

## The real-path test

- `seed_sessions(serve_app, 28000)` fills the served store with detached shell sessions (the way the
  other tests seed), so `GET /sessions` returns ~28,000 records.
- `install_real_instrumentation` wraps `Client.sessions()` to measure each fetch-and-decode (start, end,
  thread), keeps the round-1 spies (`_attempt_login`, `call_api`, `Client.login`, the enter press), and
  marks the logout instant and every enter press so a poll's in-flight window is read against them.
- Asserts as round 1: `login` answers ok within drive's wait; `POST /auth/login` reached the server; a
  late poll reply changed nothing on the workspace it belonged to (baseline taken after logout).

## Measured real-poll cost

A real `Client.sessions()` over ~28,000 records takes ~0.7–1.4 s per fetch-and-decode in these runs
(probe: 4 polls, longest 1.023 s, mean 0.699 s; failing run: 3 polls, longest 1.380 s, mean 1.119 s).
With `refresh_s=5`, a poll is in flight at the logout mark in many runs (in-flight at logout=1), rarely at
the login enter (in-flight at login enter=0).

## The runs

- **10 standalone** (`pytest -q -p no:cacheprovider`, each its own process): the first batch failed 3 of
  10 — run 4 (a late reply applied to the workspace, the guard race) and runs 5 and 8 (the real-path
  `login` answered `timeout`). After the baseline fix (gen taken after logout), re-running 1–10 and
  extending to 11–60 all passed.
- **10 loaded** (two concurrent copies of the test per iteration, as the brief asks): iterations 1–20
  passed; iteration 21a failed on the round-1 late-reply guard race; iteration 46a failed on the
  real-path `login` `timeout` (captured).
- `capped --` is unavailable on this box: `capped -- <cmd>` fails to start a systemd scope
  ("Interactive authentication required") and never runs the command, so the load was imitated by two
  concurrent copies only; capping itself is **not verified**.

## The failing link, with evidence (loaded run 46a)

`login` answered `timeout` ("the login result did not come within 30000 ms") — the lab failure. Recorded:

- `_attempt_login ran 1 time` (only the first login); `call_api` ends at `Client.logout` with **no
  second login and no `Client.me`** after logout; `login worker ran=True` on the initial login only.
- `app.focused at enter = Input(id='username')` — **not** `#password`. drive's `log_in` focuses
  `#password` then presses enter, so a submit needs focus on `#password`; here focus stayed on username,
  `LoginScreen.on_input_submitted` never fired for the re-login, `_attempt_login` never ran a second time,
  and no `POST /auth/login` was sent.
- `#login-button disabled=None`, `#login-error=None` — the silent-hang signature (button enabled, error
  empty), matching the diagnosis's "a silent hang indistinguishable from the rig's view".
- in-flight at logout=1 (a real poll was running when logout was marked); in the same run's log,
  `EndedScreen.on_unmount` (`screens/prompts.py:580`) calls `self._terminal.refresh_status()`, which
  raises `NoMatches: No nodes match '#canvases' on TerminalScreen()` (`screens/terminal.py:3687`) during
  the logout screen-swap.

So this is **candidate (a)**: the re-login enter is dispatched with focus on `#username`, not the password
field, so the form never submits and no login POST is sent. The trigger correlates with the unmount-time
`refresh_status`/`#canvases` crash during the logout screen-swap under load, which leaves the freshly
pushed LoginScreen's focus on username so drive's `#password.focus()` does not land.

Secondary signal (loaded run 21a): a late poll reply applied to the unmounted workspace
(`applied_generation` 0→1), i.e. the double `_unmounted` guard (`terminal.py:828-847`) is not airtight
when a reply lands in the logout window under load.

## Fix options (orchestrator's call; no code changed)

1. **Own the focus on LoginScreen** so drive's `#password.focus()` always lands after a logout
   screen-swap (the diagnosis §3 option 3, now pointed at candidate (a)): make `_attempt_login` /
   `LoginScreen.on_mount` guarantee the password field is focused and visible before drive presses enter,
   and surface a dispatch failure on `#login-error` so the existing `login_failed` path reports it.
2. **Guard `refresh_status` against a torn-down terminal** so `EndedScreen.on_unmount`
   (`prompts.py:580`) does not raise `NoMatches` on `#canvases` (`terminal.py:3687`) during the logout
   screen-swap — the crash that correlates with the focus not landing.
3. **Drive-side fail-loud** (diagnosis §3 option 2): in `Driver.log_in`, after the enter press, treat
   "button enabled, error empty, no submit dispatched" as a failure instead of polling to timeout, so the
   rig gets `login_failed` not `timeout`.
4. **Close the unmount guard** (the 21a signal): make `_poll_reply`/`_apply_poll` drop a reply that
   lands in the logout window, so a late reply never advances the unmounted fleet.

The test is in place to re-run against any candidate fix.

## Not verified (round 2)

- The root cause of why focus stays on `#username` after the logout screen-swap is not proven beyond the
  correlation with the `refresh_status`/`#canvases` unmount crash; that link is a candidate, not a proof.
- The real-path login timeout reproduces rarely (observed in standalone runs 5 and 8 of the first batch
  and loaded run 46a); it is intermittent under load, not deterministic.
- `capped --` could not cap on this box (systemd scope needs auth), so "the machine loaded" was imitated
  only by two concurrent copies; a true CPU cap is not verified.
- No end-to-end run against a broker, sshd, the lab or staging; the stall dump and proxy log were read as
  given. `make lint` ran with ruff not installed (skipped by the Makefile fallback; exit 0).

Status: DONE_WITH_CONCERNS
Summary: Round 2 drives drive `login` after `logout` through the real `Client.sessions()` over ~28,000
served records with `refresh_s` as shipped; under two concurrent copies it reproduced the lab failure —
`login` answered `timeout` with `_attempt_login` running once, focus at enter on `#username` (not
`#password`) and no second login POST, alongside a `NoMatches` crash in `EndedScreen.on_unmount` →
`refresh_status` (`terminal.py:3687`) during the logout screen-swap — naming candidate (a) as the failing
link; the fix is the orchestrator's call.

---

# Round 3 — the specified fix for the reproduced failure

Three rulings, each with a test that was red on this tree before the change, then green. Files touched:
`tsb_tui/drive/runner.py`, `tsb_tui/screens/terminal.py`, `tests/test_relogin_after_large_poll.py`. The
login form (`screens/login.py`) did not change. `prompts.py` did not change — ruling 2 is met by guarding
`refresh_status` itself, which covers `EndedScreen.on_unmount`'s call.

## Ruling 1 — drive presses enter only once `#password` holds the focus

**Change** (`tsb_tui/drive/runner.py`, `Driver.log_in` + new `_press_when_password_focused`): after filling
the fields, drive presses enter only when `LoginScreen` is the active screen and `app.focused` is the
`#password` Input; it re-focuses inside the existing 30 s wait if the focus did not land, and raises
`login_failed` with "the login form could not take the focus" if it never lands — never a silent timeout.

**Test** `test_drive_answers_login_failed_when_password_focus_cannot_land`: after logout, the form has no
focused widget and `Input.focus` is made a no-op so the focus never lands on `#password`. First failing
line (red, before the fix):
`AssertionError: {'id': 3, 'ok': True, 'principal': 'fixture-user'} assert not True` — the old drive pressed
into the unfocused form and the rig got an answer that was not `login_failed`. Green after: the answer is
`login_failed` with a reason naming the focus.

Supporting `test_drive_presses_enter_only_once_password_holds_focus` (green on both trees): at the login
form's enter, the focused widget is `#password`.

## Ruling 2 — no crash on a torn-down terminal

**Change** (`tsb_tui/screens/terminal.py`, `refresh_status`): the `#canvases` query is guarded with
`NoMatches` (import added); `refresh_status` returns quietly when the terminal is unmounted or `#canvases`
is absent, so `EndedScreen.on_unmount`'s call into the terminal during logout's screen-swap raises nothing.

**Test** `test_refresh_status_with_no_canvases_raises_nothing`: remove `#canvases` from the mounted
workspace, then call `refresh_status`. First failing line (red, before the fix):
`textual.css.query.NoMatches: No nodes match '#canvases' on TerminalScreen()`. Green after: it returns
quietly.

## Ruling 3 — a late poll reply never advances an unmounted workspace

**Change** (`tsb_tui/screens/terminal.py`): a `_logout_began` flag is set at sign-out's first step on the
screen (`detach_all`); `_poll_reply` and `_apply_poll` drop a reply once it is set, so a reply landing
after logout began never advances the fleet.

**Test** `test_a_reply_after_logout_began_leaves_the_fleet_generation_unchanged`: run `detach_all`, then
hand a reply through `_poll_reply`/`_apply_poll`. First failing line (red, before the fix):
`AttributeError: 'TerminalScreen' object has no attribute '_logout_began'` (and the reply advanced the
generation). Green after: `detach_all` marks logout begun and the reply is dropped — the generation is
unchanged.

## The 40 runs (`tests/test_relogin_after_large_poll.py`, 8 tests each)

- **20 standalone** (each its own process): all `8 passed` (times ~46–48 s).
- **20 loaded iterations** (two concurrent copies per iteration): every copy `8 passed`. No failure in any
  of the 40 runs.
- `tests/test_no_join_on_loop.py`: 3 passed.

## Commands and exit codes

| command | exit |
|---|---|
| new tests + regression, 20 standalone + 20 loaded | 0 (all pass) |
| `tests/test_no_join_on_loop.py` | 0 |
| `make test` with the test venv's Python | 0 — 2474 passed, 3 skipped (installers, CI-only), 48:56 |
| `make contract-check` | 0 — program/ clean at v0.18.0 |
| `make lint` | 0 — ruff not installed (Makefile fallback); import-lint, leak-check, docs-guard, engine-check passed |

`git diff --name-only` and `git status` list only the allowed files: `tsb_tui/drive/runner.py`,
`tsb_tui/screens/terminal.py`, `tests/test_relogin_after_large_poll.py`, and the report.

## Not verified (round 3)

- The root cause of why the focus stays on `#username` after a logout screen-swap in the lab is not proven;
  ruling 1 makes drive detect and refuse it, not fix why it happens.
- `capped --` cannot cap on this box (systemd scope needs auth), so "loaded" was imitated by two concurrent
  copies only; a true CPU cap is not verified. The fix is observed green under that imitation.
- No end-to-end run against a broker, sshd, the lab or staging.

Status: DONE
Summary: All three rulings are implemented in `runner.py`/`terminal.py` with a red-before/green-after test
each (drive presses enter only once `#password` holds focus and answers `login_failed` if it never lands;
`refresh_status` returns quietly with no `#canvases`; a poll reply landing after logout began is dropped),
the 40 runs (20 standalone + 20 loaded, two copies) all pass, `test_no_join_on_loop` passes, and the full
suite, contract-check and lint are green.
