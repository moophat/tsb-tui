# Phase 04 — Searchable New session and add target

**AI-Model:** gpt-6.1-sol  
**Status:** Amended review draft — orchestrator dispatch after hotfix; rulings incorporated.  
**Depends on:** integrated hotfix and preceding phase; ruled O2/O3/O6/O8 in [plan.md](plan.md).  
**Next:** P5; serial merge only.

## Scope and authority

One focused builder session under the recorded rulings. Implement only this phase’s acceptance. If the plan leaves any design decision unspecified, STOP and report the options; never choose one yourself. The orchestrator owns plans, rulings and ledgers; builders write only approved code/tests and their own report. Do not edit program/, .plan-inputs/, vendor/ or these phase files.

## Read before writing

Repository-relative paths under tsb_tui/ unless explicitly qualified: screens/new_session.py; screens/target_form.py and its result/cancel callers in screens/terminal.py; model/targets.py; P3 search; contract protocols target-write, inventory-list, session-open; OpenAPI TargetDefinition; tests/test_target_form.py and test_new_session_login.py.
Read exports, callers, tests and shared utilities at the actual merged baseline. A missing or renamed file is a context check, not permission to invent an alternate interface.

## Files allowed to write

tsb_tui/screens/new_session.py; tsb_tui/screens/target_form.py; tsb_tui/screens/terminal.py; tests/test_redesign_new_session.py; tests/test_new_session_login.py; tests/test_target_form.py; this phase’s snapshot baselines.

## Tests first and measurable targets

- **L1-new-target / L1-filter-position:** Pilot n searches the P3 inventory against literal name and host field text, including IP text, with no DNS resolution; selecting Enter uses that entity’s internal id without asking user for it. Match beyond first page; selection and position follow P3. Red: hotfix presents target-id Input and direct get-by-id.
- **L1-new-target-permissions:** No match with target.edit adds selectable last row + Add '<query>' as a target; Enter on that row or a opens the existing TargetFormScreen; operator completes required fields, save succeeds, continuation opens New session for that target per O8. No match without permission gives approved guidance, no create target request. Permission revoked after opening gets authoritative 403, no session create. Cancel, 409, 422, 500 unknown-effect and 503 never cause automatic mutation retries. Red: no integrated no-match/add-target continuation.
- **L1-focus / L1-escape / L1-geometry:** Keys-only form traversal, cancel returns exactly one level with query intact; snapshots at both sizes for matches/no-match/form/error/connection choice/credential choice. No control clips. Session create always carries actual window size with auto-size on and off; no default credential or connection silently chosen.

Every relevant Pilot case uses keys and captures reviewed SVGs at 80x24 and 160x45. Assert focus, rendered text/style and geometry separately from snapshots. Run against fixtures/test doubles, never a workstation broker or sshd. Preserve green hotfix regressions; the phase’s stated new behavior must fail on hotfix, not merely on missing test infrastructure.

## Amendment 1 — continuation boundary

Use P3’s literal name/host-text search, position and no-login-words projection; P4 does not wait for the login-hint contract or add availability guesses. For a target.edit holder the no-match selectable last row is exactly + Add '<query>' as a target; Enter or a opens the existing form. Without that permission no Add row or target-create request is offered. No DNS resolution. Preserve the exact no-match copy and Esc-clears recovery from P3, and test both-size snapshots for both permission states. Ruled O8 specifies no inferred form fields and continuation using the record returned by save; Cancel creates nothing, refusal is shown and never retried.

## Steps

1. Write red Pilot search and continuation tests first, without changing existing TargetDefinition or fixture schemas.
2. Replace target-id entry with the P3 search path; preserve existing explicit connection, credential, device-login and recording-consent branches.
3. Connect the exact selectable last Add row and Enter/a actions to the existing create form and approved result callback; do not implement a second target writer or infer protocol/id.
4. Keep target.edit gate and server refusals; drop stale async replies when query, login or screen changes.
5. Run target-form, connection-choice, device-login, size and new-session regressions.

## Gate and evidence

Before implementation, record the applicable ruling references and exact files/test function names in the orchestrator’s dispatch brief. Capture red assertion output on the hotfix baseline; implement; rerun the same tests green. The new test files above are planned names, not commands that can run before they exist. Use `env -u NO_COLOR python -m pytest <this phase’s test files> -p no:cacheprovider`, plus the named existing regression suites. Check actual test filenames before dispatch.

Gate: all phase assertions pass, both-size baselines reviewed, no new skips/xfails, no out-of-scope paths in `git diff --name-only`, and `make contract-check`, `make lint`, `make test` pass on the integrated branch with NO_COLOR unset. A dependency/setup failure is reported as unverified, never green. The phase report includes exact commands, output and red/green evidence; the orchestrator verifies it before the next merge. Stop every process started; no background command survives handoff.
