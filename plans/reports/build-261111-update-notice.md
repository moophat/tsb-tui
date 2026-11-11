# Phase 9 update notice

**AI-Model:** gpt-6.1-sol

## Delivered

The app starts the update check after its first screen is painted. Release builds check the static manifest using a no-redirect, unauthenticated GET, with TLS verification using the app's CA file and a 64 KiB read cap. The UI remains interactive and the caller's entire wait is bounded to 1.5 seconds. Development version `0.0.0` skips the check.

A short information toast points to the existing Settings view. That view carries the full version and platform-specific command as selectable text. The command comes from the configured manifest URL and fixed installer suffix, never from manifest fields. A cached newer version is announced on every start inside the interval without another request.

Registered `update.check`, `update.interval_h` (minimum 1, default 24) and `update.url`. Only the drive and harness profiles disable the feature. The documented off switch is `TSB_TUI_UPDATE_CHECK=0`, with the config key and `--set update.check=false` alternatives. Disabled checks make no request or cache write.

The cache is written atomically in the per-user state directory with owner-only mode where supported. The orchestrator's 03:2x ruling is implemented: a failed request records its attempt time and retains any previous latest; a first failure writes JSON null. A successful answer replaces latest even when equal or older. Null, absent, invalid and non-newer cached versions announce nothing.

## Import-lint ruling

The orchestrator's 2026-11-11 03:4x ruling authorizes exactly `urllib.request` and `ssl` in exactly `tsb_tui/update_check.py`, because this is a static nginx manifest rather than a broker API. The named allowance is `UPDATE_CHECK_ALLOWED`, with its reason and per-import rationale. Broker API calls still go through the contract client.

Removed the checker's other network imports and low-level connection/socket machinery. Origin and authority come from the public `urllib.request.Request` API; no forbidden module is accessed through an alias or re-export.

The regression tests prove that the two allowed imports still fail in other modules, including a different file named update_check, and that `socket`, `http.client`, `urllib.parse`, whole `urllib`/`http` packages and `ssl.extra` remain forbidden in the checker.

## Failing-first evidence

- Before the checker existed, the initial update tests failed collection on the missing module.
- Before registering the knobs, all four new profile tests failed on missing `update.check`.
- Before adding the import allowance, `make lint` reached the new lint tests: **2 failed, 78 passed in 8.12s**. Both intended passing imports were rejected; negative boundary tests already passed.
- The pending nested-context style finding was fixed.

## Verification

Commands ran in the foreground using the test venv via the Makefile's PYTHON override. New HTTP tests used loopback stub servers only.

- `make lint`: **PASS**. Ruff passed; **80 support tests passed in 8.03s**; import-lint, leak check and vendored-engine verification passed. The docs branch guard reports its normal no-event no-op outside a pull-request event.
- `make test`: **1971 passed in 1836.09s (0:30:36)**, no skips or deselections. The pinned contract check passed first.
- The suite includes the update/configuration tests: loopback request counters, redirect refusal, request headers, explicit URL override, cache interval/corruption, failures retaining known latest or writing null, successful replacement by an older version, all disable paths, development metadata, platform commands, interactive login during a slow request, and repeated app starts reusing the cache.
- The Settings pilot test extracts the complete notice through Textual's selection API and checks one information toast per app run.
- Atomic cache permissions and failed-replace cleanup are covered.
- `git diff --check`: **PASS**.
- Changed-path, Python syntax and newly added machine-path audit: **PASS**, 12 authorized files. The supplied untracked brief was excluded from the delivery audit.

The initial baseline suite from the earlier continuation was interrupted after implementation edits made it unsuitable as baseline evidence. The full passing run above is a fresh run of the final implementation.

## Checkpoint

- [x] Inspect the app, registry, state writer and existing Settings view.
- [x] Implement the checker, configuration, profiles, worker, notice and documentation.
- [x] Implement both cache rulings and the exact-module import allowance.
- [x] Verify failing-first lint tests, final lint and the full suite.
- [x] Audit final paths and record evidence and limitations.

No tracker/session files or program submodule files were changed. No rotation script was run during these continuations. No commits or pushes were made.

## Concern and unverified environments

The 1.5 second deadline bounds the caller's entire wait, including DNS. A daemon performs the I/O and cannot touch UI or cache; it checks the deadline before opening and between reads. An operating-system DNS/socket operation already pending can outlive the caller. Under the narrowed import allowance there is no low-level socket cancellation. The passing timing test proves UI responsiveness and worker completion, not physical cancellation of all network activity at the deadline.

Real-instance, TLS, Windows and macOS runtime proofs were not performed. Those remain environment proofs rather than claims of this loopback suite.

Status: DONE_WITH_CONCERNS

## Qodo round 1

**AI-Model:** gpt-6.1-sol

Applied the orchestrator's 2026-11-11 14:4x dispositions for PR #54, starting from head `b56ca9a`.

- Cache entries now store the exact manifest `url`. A different or absent URL makes the entry absent for this check, so changing broker or `update.url` checks immediately and cannot retain another source's update on failure. Existing cache fixtures now identify their source.
- A notice arriving while Settings is open refreshes its selectable line immediately. Other open text views are left alone.
- Before consulting the cache or making a request, the manifest URL must use HTTPS, or HTTP with a loopback host; its path must end in `/latest.json`; and every character must be an ASCII letter, digit or one of `-._~:/%`. Invalid configured URLs silently disable checking and notices, including cached notices, without changing the cache or failing startup. Query strings, fragments, whitespace, quotes and shell characters are rejected. Validation and origin extraction use string operations, with no URL-parser import.
- The installer base removes the final manifest path segment and appends the fixed platform installer name.
- A future `checked_at` is due immediately, so moving the clock back triggers a fresh request.

### Red-first evidence on b56ca9a

Added only regression tests first, leaving production files at `b56ca9a`, then ran `make test` with the test venv and `PYTEST_ADDOPTS='-q -k qodo'`:

**21 failed, 1971 deselected in 10.87s.**

The failures cover both broker and override URL changes, a late notice in already open Settings, invalid fresh and cached manifest URLs (including non-loopback HTTP), and a future cache timestamp. Invalid-URL tests replace fetching with a fail-fast spy, so they make no external network request.

After implementation, the same red-first selection reported **21 passed, 1971 deselected in 11.35s**.

### Final verification for this round

All commands ran in the foreground with the test venv through the Makefile's PYTHON override.

- `make lint`: **PASS**. Ruff passed; **80 support tests passed in 8.08s**; import-lint, leak check and engine verification passed. The docs branch guard reports its usual no-event no-op outside a PR event.
- `make test` with `PYTEST_ADDOPTS='-q -k "test_update_check or test_settings"'`: **66 passed, 1926 deselected in 33.03s**. This selects the update-check module and every existing `tests/test_settings*` module, including the new late-notice pilot.
- `git diff --check`: **PASS**.
- Import-lint allowance and its tests: unchanged. No `urllib.parse`, socket or other network imports were added.
- No commits, pushes, tracker rotation or changes under `.aiworkflow/` or `program/`.

The full suite was not rerun in this bounded round; the preceding full-suite evidence applies to the earlier head only. The existing daemon I/O deadline concern and unverified platform/environment proofs described above remain unchanged.

Status: DONE_WITH_CONCERNS

## Qodo round 2

**AI-Model:** deepseek-v4.1-flash-free

Applied the orchestrator's 2026-11-11 16:4x ruling at head `4127ea5`: a bracketed IPv6 host is accepted in the manifest
address. `[` and `]` are allowed only as the pair around the host immediately after the scheme's `//`; any other
bracket, an unbalanced bracket or an empty bracket fails. The loopback check reads the host inside the brackets, so
`::1` is loopback like `127.0.0.1` and `localhost`. Everything else in `_safe_url` is unchanged: HTTPS, or HTTP with a
loopback host; the path must end in `/latest.json`; no query, fragment, whitespace, quote or shell character; only
letters, digits and `-._~:/%` elsewhere. Validation stays string-only, with no new import and no URL parser.

### Red-first evidence on 4127ea5

Added the regression tests only, then ran the new selection on the unchanged head: **2 failed, 8 passed, 63
deselected**. The two failures are the accepted addresses
(`http://[::1]:8080/downloads/tsb-tui/latest.json` and `https://[2001:db8::1]/downloads/tsb-tui/latest.json`); the
refusal cases already held, as expected. No request was made: the acceptance tests replace fetching with a recording
double, and the refusal tests use a fail-fast one.

### Verification after implementation

- New selection: **10 passed, 63 deselected** in 0.68 s.
- `tests/test_update_check.py`: **73 passed** in 24.66 s.
- `make lint` with the test venv: **PASS**. Ruff passed; **80 support tests passed**; import-lint clean over 183
  files; leak check and vendored-engine verification passed.
- `git diff --check`: **PASS**.
- No commits or pushes; nothing under `.aiworkflow/` or `program/` was touched.

Status: DONE
