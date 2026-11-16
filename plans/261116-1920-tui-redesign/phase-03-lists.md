# Phase 03 — Inventory and session rows

**AI-Model:** gpt-6.1-sol  
**Status:** Amended review draft — O1 never blocks P3; O2/O3/O6 ruled.  
**Depends on:** integrated hotfix and P2b; ruled O2/O3/O6 in [plan.md](plan.md).  
**Next:** P4; serial merge only.

## Scope and authority

One focused builder session under the recorded rulings. Implement only this phase’s acceptance. If the plan leaves any design decision unspecified, STOP and report the options; never choose one yourself. The orchestrator owns plans, rulings and ledgers; builders write only approved code/tests and their own report. Do not edit program/, .plan-inputs/, vendor/ or these phase files.

## Read before writing

Repository-relative paths under tsb_tui/ unless explicitly qualified: model/fleet.py; model/targets.py; widgets/sidebar.py; screens/fleet_view.py; screens/terminal.py; contract protocols inventory-list and session-list; OpenAPI TargetRecord; tests/test_targets_tab.py and test_model_fleet.py.
Read exports, callers, tests and shared utilities at the actual merged baseline. A missing or renamed file is a context check, not permission to invent an alternate interface.

## Files allowed to write

tsb_tui/model/fleet.py; tsb_tui/model/targets.py; tsb_tui/widgets/sidebar.py; tsb_tui/screens/fleet_view.py; tsb_tui/screens/terminal.py; tests/test_redesign_lists.py; tests/test_model_fleet.py; tests/test_targets_tab.py; this phase’s snapshot baselines.

## Tests first and measurable targets

- **L1-targets-first / L1-row-grammar:** Fixture login opens cockpit with inventory above sessions. Assert open-here numbers, session state, owner and typer independently of colour. Target rows contain no login words: neither login saved nor no saved login: type it at connect, nor invented substitutes. P3b adds those later. Red: sidebar has sessions only; P3 is independent of O1.
- **L1-ended / L1-filter-position:** Default excludes killed/terminated from every session list/chip as a hotfix regression assertion, not new red evidence. New failing assertions: e toggles show ended within a session list and shown rows contain killed/terminated end words. Needs-you/open-here/yours live/yours detached/joinable order asserted using shuffled records, identity retained over reordered refresh. 50 and 8,000 rows, target paging through published cursors, match beyond first page against literal name and host text, including IP text; assert no DNS call; header index and denominator equal O6 semantics. Red: no shared free-text inventory search/position.
- **L1-list-keys / L1-empty:** Pilot /, text, Enter, n and ? without leader in each list; list typing follows O3 input precedence, no PTY leakage. Empty/no-match/error/loading/permission states assert the exact Amendment 1 copy below and exercise each advertised recovery key. Delayed old query cannot overwrite current results; selection removal follows O6. Red: target queries accept structured filters rather than the ruled free-text behavior.
- **L1-focus / L1-geometry:** Two-size populated/empty/stale/ended-enabled snapshots, full-width highlight, visible selected row and index after moving to item 8,000; all required words reachable using the approved narrow-row presentation.

Every relevant Pilot case uses keys and captures reviewed SVGs at 80x24 and 160x45. Assert focus, rendered text/style and geometry separately from snapshots. Run against fixtures/test doubles, never a workstation broker or sshd. Preserve green hotfix regressions; the phase’s stated new behavior must fail on hotfix, not merely on missing test infrastructure.

## Amendment 1 — exact rows and copy

- **L1-row-budget:** At 28 outer/24 inner columns, target row is two lines: name (host if unnamed; name ends in ellipsis if longer), then host · open N,M, omitting open-number suffix when none. Session row is up to three lines: N name; state · owner; you type / <name> types / nobody typing. No word is cut. At 160 columns each target/session row is one line. Test zero/one/multiple open numbers. For an input that cannot fit this exact budget without cutting a word, stop and report it rather than silently changing the ruling.
- **L1-empty:** target.edit empty = No targets yet · a add one; other empty = No targets you can see; no-match = No target matches '<query>' · Esc clears; loading = Loading targets… / Loading sessions…; error = Could not load targets · r retry / Could not load sessions · r retry; sessions empty = No live sessions · n new session; only ended = No live sessions · e show ended. Assert exact strings and a/r/n/e/Esc actions in their contexts. Render all as plain text.

## Ruled O3/O6 — search and selection

n opens New session in every inventory/session list; a Add target appears only to target.edit holders. Typing in an editable field remains typing. / enters the filter; Esc exits and clears it, then a second Esc follows P2b’s list rule. Match literal name/host fields, including IP text; never resolve DNS. Load every published page before reporting filtered-result position or final no-match. Highlight follows entity identity; when removed, choose the next surviving row.

## Steps

1. Proceed without O1: omit all target login words and availability inference. Record P3b as the later contract-dependent addition; no new contract field is used in P3.
2. Under the recorded rulings, add failing tests using pinned fixtures and test doubles; keep already-passing ended-sidebar tests as regressions.
3. Implement literal local name/host-text filtering (including IP text), with no DNS resolution, over authorized public inventory pages and approved presentation sorting; do not invent query parameters or duplicate backend authorization.
4. Reuse the resulting projection/filter for cockpit and full view, preserving literal words and identity; remove only render paths superseded by this change after parity.
5. Verify no false no-match during incomplete loading; no hidden ended records reappear after refresh or filter reset.

## Gate and evidence

Before implementation, record the applicable ruling references and exact files/test function names in the orchestrator’s dispatch brief. Capture red assertion output on the hotfix baseline; implement; rerun the same tests green. The new test files above are planned names, not commands that can run before they exist. Use `env -u NO_COLOR python -m pytest <this phase’s test files> -p no:cacheprovider`, plus the named existing regression suites. Check actual test filenames before dispatch.

Gate: all phase assertions pass, both-size baselines reviewed, no new skips/xfails, no out-of-scope paths in `git diff --name-only`, and `make contract-check`, `make lint`, `make test` pass on the integrated branch with NO_COLOR unset. A dependency/setup failure is reported as unverified, never green. The phase report includes exact commands, output and red/green evidence; the orchestrator verifies it before the next merge. Stop every process started; no background command survives handoff.
