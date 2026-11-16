# Phase 06 — Complete gallery and CI UX gate

**AI-Model:** gpt-6.1-sol  
**Status:** Amended review draft — orchestrator dispatch after hotfix; rulings incorporated.  
**Depends on:** integrated hotfix and preceding phase; P1/P2a/P2b/P3/P4/P5 green; P3b is deferred and not a prerequisite in [plan.md](plan.md).  
**Next:** release review; later P3b after its contract release, serial merge only.

## Scope and authority

One focused builder session under the recorded rulings. Implement only this phase’s acceptance. If the plan leaves any design decision unspecified, STOP and report the options; never choose one yourself. The orchestrator owns plans, rulings and ledgers; builders write only approved code/tests and their own report. Do not edit program/, .plan-inputs/, vendor/ or these phase files.

## Read before writing

Repository-relative paths under tsb_tui/ unless explicitly qualified: tests/test_snapshots.py; tests/test_minimum_contrast.py; tests/conftest.py; tests/test_discoverability.py; all screens and screen callers; pyproject.toml; .github/workflows/; FM01 input testing plan.
Read exports, callers, tests and shared utilities at the actual merged baseline. A missing or renamed file is a context check, not permission to invent an alternate interface.

## Files allowed to write

tests/test_redesign_gallery.py; tests/test_redesign_focus.py; tests/test_redesign_contrast.py; tests/conftest.py; tests/redesign_screen_manifest.json; this phase’s snapshot baselines; pyproject.toml and the existing test workflow only if layer-1 integration is missing.

## Tests first and measurable targets

- **L1-gallery:** Inventory every Screen subclass and material state in a manifest; every entry has 80x24 and 160x45 committed SVGs and a keys-only route. Assert no manifest omissions. Red against hotfix: target-first cockpit, searchable New session and prompt-decorated gallery assertions fail; absence of baseline files alone is not accepted red evidence.
- **L1-focus / L1-hints / L1-escape:** Full login → targets → open → attach consent → session → leader → sidebar/full view → new → cancel flow, plus Vault/All alerts and every other screen. Assert exact focus chain and reverse traversal, viewport-contained hint row and Esc behavior. Existing locks retain their restrictions.
- **L1-contrast / L1-geometry:** All shipped themes and both sizes, populated/empty/stale/ended/error states. Fail on missing/xfail UX cases including #93; compare effective text colours and geometry. Captured gallery and actionable failures are available to FM01, without environment addresses or secrets.

Every relevant Pilot case uses keys and captures reviewed SVGs at 80x24 and 160x45. Assert focus, rendered text/style and geometry separately from snapshots. Run against fixtures/test doubles, never a workstation broker or sshd. Preserve green hotfix regressions; the phase’s stated new behavior must fail on hotfix, not merely on missing test infrastructure.

## Amendment 1 — amended gallery coverage

Assert worded rail from width 81 and exact compact Unicode/ASCII marks at <=80; + New last; 28-column side panel beside canvas; active ━ and inactive ─ tab underlines; exact Rail→Targets→Sessions chain and reverse; canvas Tab/Esc bytes unchanged; list Esc canvas-return/no-open behavior; exact empty/error copy and recovery keys; e show-ended switch and end words; literal name/host search with no DNS; exact last Add row and Enter/a. P3 target rows have no login words. O1/P3b is tracked as deferred work rather than a skipped/xfail acceptance within P3’s release. Final pick-10 login-word acceptance remains explicitly incomplete until P3b ships.

## Steps

1. Write gallery inventory/assertions first; identify substantive red behavior on hotfix, then verify P1–P5 fix it. Do not fabricate red/green logs.
2. Reuse the existing snapshot mechanism if equivalent to pytest-textual-snapshot; inspect its committed baselines before adding a dependency. Integrate with make test, no separate optional gate.
3. Ensure no skip/xfail stands in for a ruled requirement; remove #93 waiver only when keys-only reachability proves it.
4. Run focused UX tests, then make contract-check, make lint and make test with NO_COLOR unset. Report counts, failures and any skips explicitly.
5. Orchestrator hands the released binary/gallery to FM01 for real-terminal and vision review; keep release approval distinct from headless CI.

## Gate and evidence

Before implementation, record the applicable ruling references and exact files/test function names in the orchestrator’s dispatch brief. Capture red assertion output on the hotfix baseline; implement; rerun the same tests green. The new test files above are planned names, not commands that can run before they exist. Use `env -u NO_COLOR python -m pytest <this phase’s test files> -p no:cacheprovider`, plus the named existing regression suites. Check actual test filenames before dispatch.

Gate: all phase assertions pass, both-size baselines reviewed, no new skips/xfails, no out-of-scope paths in `git diff --name-only`, and `make contract-check`, `make lint`, `make test` pass on the integrated branch with NO_COLOR unset. A dependency/setup failure is reported as unverified, never green. The phase report includes exact commands, output and red/green evidence; the orchestrator verifies it before the next merge. Stop every process started; no background command survives handoff.
