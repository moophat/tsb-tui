# fix/69 — update-check wall-clock bound → property assertion

**Model:** strata/strata | **Started:** 2026-11-13
**Branch:** fix/69-update-check-property @ a59b2a8
**Allowed paths:** `tests/test_update_check.py`, this report.

## Task

`tests/test_update_check.py::test_timeout_retains_latest_and_caches_attempt` (tests/test_update_check.py:280-291)
wraps `update.decide(...)` against a 5 s-delaying manifest server in a wall-clock bound
`time.monotonic() - started < 2`. On a loaded CI runner it measured 2.27 s and failed. The bound it
means: **decide does not wait on the network past its own timeout.** The ruling asks to replace the
wall-clock assertion with a direct one: patch the fetch decide uses so it records the timeout decide
passes and raises the real fetch's timeout error, then assert decide passed its configured timeout
(the value from `cfg(...)`, not a literal), still returns `previous`, and caches the attempt.

## Inspection (read before writing)

- `decide` lives in `tsb_tui/update_check.py:172`. Its network call is `tsb_tui/update_check.py:188`:
  `fetched = fetch_latest(url, ca_file, version=version)`. **No timeout argument is passed.**
- `fetch_latest` (tsb_tui/update_check.py:86) signature is `fetch_latest(url, ca_file=None, *, version=None)` —
  there is **no timeout parameter**. The timeout it uses is the module constant `TIMEOUT_S = 1.5`
  (tsb_tui/update_check.py:22), baked into the deadline at tsb_tui/update_check.py:100 and applied to the
  opener and the result queue (tsb_tui/update_check.py:114, 134). It is **not** read from config.
- Config exposes exactly three update knobs — `update.check`, `update.interval_h`, `update.url`
  (tsb_tui/config.py:95-97, 220-222). There is **no network-timeout key**; `cfg(...)` carries no value
  the test can read as "the configured timeout". `update.interval_h` is the hours between checks, not a fetch timeout.
- The real `fetch_latest` **never raises a timeout error.** On timeout `result.get(timeout=...)` raises
  `queue.Empty`, caught at tsb_tui/update_check.py:135-136 → returns `None`. All exceptions are swallowed
  (tsb_tui/update_check.py:125-128). `decide` retains `previous` precisely because `fetched is None`
  (tsb_tui/update_check.py:189-190), then caches the attempt. There is no "timeout error the real fetch
  raises" to reproduce in a patched fetch.

## STOP — design gap, not chosen

The plan's prescribed test cannot be written in **test-only** code, and the task names this exact stop:
"If decide has no seam a test can patch without changing `update.py`, or the timeout it uses is not a
value the test can read from the config, STOP and report the options." Both halves are true here:

1. **No timeout seam.** A monkeypatch of `update.fetch_latest` (the seam other tests already use, e.g.
   tests/test_update_check.py:159, 322, 347) records the args decide passes — `(url, ca_file)` and
   `version=version`. It records **no timeout**, because decide passes none. There is nothing to assert
   as "the timeout decide passed".
2. **No config timeout.** The value the plan wants asserted ("the configured timeout from `cfg(...)`")
   does not exist in the config. `TIMEOUT_S` is a hardcoded constant, so any literal in the test would be
   an invented number — explicitly forbidden by the ruling.
3. **No real timeout error.** "raise the timeout error the real fetch raises" has no matching real
   behaviour: the real fetch returns `None` on timeout and never raises. Patching the fetch to raise
   would exercise a code path the production code does not take, and `decide`'s `previous`-retention is
   driven by `fetched is None`, not by a raised exception.

Making the prescribed assertion possible requires changing `update.py` (give `fetch_latest` a timeout
parameter that `decide` passes) **and** adding an update-timeout config key so `cfg(...)` carries it.
That is a code + config change, not test-only, and touches the wire/timeout semantics — outside this
lane's allowed paths. Per AGENTS.md ("A design gap stops a lane … It never chooses") I stop and report;
I did not invent a literal, edit `update.py`/`config.py`, or pick an option.

## Options (report, not choose)

- **A. Give the fetch timeout a config-derived seam.** Add an update network-timeout knob to config,
  pass it from `decide` into `fetch_latest`, then the prescribed test is writable. This is a code +
  config change (and arguably a contract/semantics decision), not test-only — needs an orchestrator ruling.
- **B. Keep the wall-clock bound, make it CI-robust.** Loosen/rebase the `< 2` bound so it does not fail
  under load. This does not directly assert the property the plan wants; the ruling asked for a direct
  assertion, so this is against the stated intent.
- **C. Assert at the fetch seam without a timeout value.** Patch `update.fetch_latest` to record its args
  and return `None`, then assert decide returns `previous` and caches the attempt. This asserts decide's
  handling of a nullled fetch, **not** that decide passed a configured timeout — it does not satisfy
  "assert decide passed its configured timeout from `cfg(...)`", and drops the property the test means.

None of these is implementable as a pure test-code change that satisfies the ruling as written; A needs
a code change the lane is barred from, C drops the required assertion, B contradicts the stated intent.

## Ruling applied (orchestrator, 2026-11-13)

"You stopped rightly. Option C, plus a fetch-level test; test code only, no wall-clock assertion anywhere."
The `TIMEOUT_S` seam and the config timeout do not exist, so the property is split across two tests that
each assert a real, observable fact — no invented literal, no `time.monotonic()` bound, no change to
`tsb_tui/update_check.py` or `config.py`.

### 1. `test_timeout_retains_latest_and_caches_attempt` (tests/test_update_check.py:280)

Kept its name and parametrisation over `previous` (`[None, "0.10.0"]`). It monkeypatches
`update.fetch_latest` to record the url it is called with and return `None` — exactly what the real fetch
returns on timeout (tsb_tui/update_check.py:134-136). No manifest server, no timing. Asserts:
- `decide(...)` returns `previous` (the retained latest),
- the cache equals `{"checked_at": 100000, "latest": previous, "url": url}`,
- the fetch was called once, with the manifest url (`calls == [url]`).

This asserts decide's handling of a nullled fetch: it keeps the known latest, caches the attempt, and
reaches the network exactly once via the manifest url. It does not assert a timeout value, because there
is none to pass — that is test 2's job at the fetch seam.

### 2. `test_fetch_latest_returns_none_when_the_answer_comes_after_its_timeout` (tests/test_update_check.py:295)

Uses the existing `manifest_server(delay=5)`, monkeypatches the module's `TIMEOUT_S` to `0.2`, calls
`fetch_latest(url, version="0.4.3")` for the manifest url, asserts it returns `None`, then waits for the
request to be recorded — polling every 10 ms up to a 5 s bound — and asserts `len(requests) == 1`.

Review fix (orchestrator, 2026-11-13): the original `assert len(requests) == 1` ran the instant
`fetch_latest` returned at 0.2 s, but the server appends to `requests` only when its handler starts, which
on a loaded CI runner can land after 0.2 s — that measured-time check is the exact flake #69 removes. It is
now a bound on a condition (poll until `requests` has an entry, ≤5 s), never a measured-time assertion, so
the test cannot fail by timing. No import was left unused (`time` is used by the poll).

Why this fails if fetch waited past its timeout: the manifest handler sleeps 5 s before answering, so the
version `"0.10.0"` reaches the client only after the 0.2 s deadline. `fetch_latest` bounds its whole wait
at `deadline = time.monotonic() + TIMEOUT_S` (tsb_tui/update_check.py:100) and returns `None` at that
deadline (tsb_tui/update_check.py:134-136). If fetch instead waited for the answer, it would return
`"0.10.0"`, not `None`, and this assertion fails — pinning "fetch stops at its own timeout" independent of
CI load.

## Gate

Setup (not a code change): the pinned contract commit `9cda58f7` is unreachable over the network here, so
`program/` was populated offline from a local tsb-program clone that holds that commit and its `v0.16.0`
release tag, using `tools/contract-pin.sh`'s exact init mechanics; `make contract-check` then exits 0. Tests
run under the shared test environment (it has `textual`), with `NO_COLOR` unset per `make test`.

Review fix (orchestrator, 2026-11-13): my first gate run reported `make lint` exit 0 because ruff was not on
that shell's PATH and `make lint` skipped it — that was not the orchestrator's clone. With ruff present the
clone's lint exits 2 on the `return None` at the end of `fake_fetch` (tests/test_update_check.py:292; ruff
RET501 + PLR1711). Fix applied: that line is removed, so `fake_fetch` returns `None` implicitly, which is
what the test needs.

Re-run with ruff on PATH, writing to log files and reading the exit codes:
- `make lint > lint.log 2>&1; echo $?` → **exit 0** (ruff 0.16.9 "All checks passed!"; import-lint clean,
  engine-check OK, leak/docs guards pass).
- `python3 -m pytest tests/test_update_check.py -q > tests.log 2>&1; echo $?` → **exit 0**, **74 passed**.
The two log files were deleted after reading.

No commit, no push. Only `tests/test_update_check.py` and this report changed; `tsb_tui/update_check.py`
and `config.py` untouched.

Status: DONE
Summary: Applied the ruling (rewrite `test_timeout_retains_latest_and_caches_attempt` to assert decide retains `previous`, caches the attempt, and calls the fetch once with the manifest url; add a fetch-level test shrinking `TIMEOUT_S` to 0.2 against a 5 s server asserting `fetch_latest` returns `None`), then made the request-count assertion a condition-bound poll, then removed the `return None` that tripped ruff RET501/PLR1711; `make lint` exits 0 with ruff running and `pytest tests/test_update_check.py -q` passes 74, all in test code only.
