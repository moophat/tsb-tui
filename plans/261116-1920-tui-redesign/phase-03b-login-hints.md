# Phase 03b — Target login hints after contract release

**AI-Model:** gpt-6.1-sol  
**Status:** WAITING — login-hint public contract release; no implementation dispatch.  
**Depends on:** P3 integrated; serialize against P4/P6 where paths overlap; separate program/core capability released and pinned by its own PR. **Next:** final pick-10 acceptance and updated gallery.

## Scope and authority

Amendment 1 moves only target login words out of P3 into this later phase. P3 never waits for this work. The external design is tracked at tsb-core plans/reports/design-261116-target-login-hint.md; that document is an external reference, not a file inspected or copied into this repo. Read its approved design and the actual released public protocol/schema before revising this phase for dispatch. Do not predict a field name, status enum, endpoint, fallback or availability algorithm. If the plan leaves any design decision unspecified, STOP and report the options; never choose one yourself.

## Read before writing

At dispatch read the newly pinned contract’s protocols before schemas/fixtures; P3’s projection, tsb_tui/model/targets.py, widgets/sidebar.py, screens/fleet_view.py, screens/new_session.py and their callers; public contract client exports for the released capability. Read final approved row budget and any 28-column login-word presentation ruling.

## Files allowed to write

Provisional scope for orchestrator confirmation after release: tsb_tui/model/targets.py; tsb_tui/widgets/sidebar.py; tsb_tui/screens/fleet_view.py; tsb_tui/screens/new_session.py; tests/test_redesign_login_hints.py; this phase’s snapshot baselines. Any additional call-site path requires an explicit brief update. No program/, core, vendor, drive or contract-pin write in this phase.

## Tests first and measurable targets

- **L1-login-hint:** Fixture-backed public hint data drives the final ruled login saved / no saved login: type it at connect words, never a guess based on personal credential lists or a create probe. Define exhaustive public status mappings only after the release exists. New failing assertion against hotfix and pre-P3b code: the published hint is rendered truthfully rather than omitted.
- **L1-row-budget / L1-geometry:** Both 80x24 and 160x45 snapshots, no word cut and no clipped hint. P3’s two-line target format currently reserves no login-hint line; resolve its presentation before building P3b rather than silently changing P3’s ruled budget.
- **L1-focus / L1-contrast:** Keys-only list/open/new flow, same P2b focus chain, no automatic create/credential choice, effective decorated text contrast >=4.5 under approved scope. Refresh/failure/unknown statuses use only released semantics; no invented fallback.

## Steps

1. Wait for the independent capability release/pin; this is not a stop in P3. Confirm approved protocol, client exports, fixtures, status-to-word mapping and compact presentation.
2. Orchestrator updates dispatch scope and tests from the actual release. Capture substantive red rendering assertions without hand-writing contract definitions.
3. Consume public hints as data; add words to the approved target views. Do not resolve credentials locally or mutate sessions to discover availability.
4. Repeat P3/P4 regressions, focus/contrast and two-size gallery. Mark final pick 10 complete only after this gate passes.

## Gate and evidence

Before implementation, record the applicable ruling references and exact files/test function names in the orchestrator’s dispatch brief. Capture red assertion output on the hotfix baseline; implement; rerun the same tests green. The new test files above are planned names, not commands that can run before they exist. Use `env -u NO_COLOR python -m pytest <this phase’s test files> -p no:cacheprovider`, plus the named existing regression suites. Check actual test filenames before dispatch.

Gate: all phase assertions pass, both-size baselines reviewed, no new skips/xfails, no out-of-scope paths in `git diff --name-only`, and `make contract-check`, `make lint`, `make test` pass on the integrated branch with NO_COLOR unset. A dependency/setup failure is reported as unverified, never green. The phase report includes exact commands, output and red/green evidence; the orchestrator verifies it before the next merge. Stop every process started; no background command survives handoff.
