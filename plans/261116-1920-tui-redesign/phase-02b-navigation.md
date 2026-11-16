# Phase 02b — Focus and navigation

**AI-Model:** gpt-6.1-sol  
**Status:** Amended review draft — orchestrator dispatch after hotfix; rulings incorporated.  

## Scope and authority

One builder session, serial with all other phases touching these files. If the plan leaves any design decision unspecified, STOP and report the options; never choose one yourself. No code, contract or vendor change in this planning lane. Builders do not edit plans or ledgers.

**Depends on:** P2a; ruled O3/O5 for F1/edit contexts and Vault/Alerts destinations. **Next:** P3. No login-hint contract dependency.

## Read before writing

Read tsb_tui/screens/terminal.py, widgets/rail.py, widgets/sidebar.py, screens/fleet_view.py, screens/prompts.py (AlertPopup and AlertsScreen), model/actions.py and app.py; existing focus/discoverability/first-use leader tests and P2a geometry. Inspect existing leader bindings rather than invent a canvas entry/exit key.

## Files allowed to write

tsb_tui/widgets/rail.py; tsb_tui/widgets/sidebar.py; tsb_tui/screens/terminal.py; tsb_tui/screens/fleet_view.py; tsb_tui/screens/prompts.py; tsb_tui/model/actions.py; tsb_tui/app.py; tests/test_redesign_focus.py; tests/test_discoverability.py; this phase’s snapshot baselines.

## Ruled focus chain

| Current chrome stop | Tab | Shift+Tab | Internal navigation / activation |
|---|---|---|---|
| Rail (one stop) | Targets list | Sessions list | Up/Down move rail row; Enter activates it |
| Targets list | Sessions list | Rail | List keys; Enter opens target’s New session flow |
| Sessions list | Rail | Targets list | List keys; Enter enters selected open/attached session canvas |

Canvas is outside the chrome Tab chain. Enter on a session row or the existing leader binding enters it; only the existing leader binding leaves it. Tab and Esc on an attached canvas go to the session unchanged, preserving the hotfix ruling. Esc in a list returns to an open session’s canvas, and does nothing when none is open. The advertised no-match Esc clears that query. Dialog Esc backs one level without quitting.

## Tests first and measurable targets

- **L1-focus:** Pilot Tab Rail→Targets→Sessions→Rail and Shift+Tab in reverse; record exact focus owner, assert one visibly bordered pane. Up/Down on Rail do not create extra Tab stops. Assert terminal receives no chrome navigation frames. Red: existing sidebar controls/hidden rail do not implement this chain.
- **L1-rail-new:** + New is last and selectable; Enter there or n on Rail opens New session, without a leader. Snapshot the selected full-width bar at both sizes. Red: no worded persistent rail with this row/chain.
- **L1-compact-marks:** Activate each compact row by Up/Down/Enter; assert exact Unicode/ASCII marks from P2a, no ambiguous replacement.
- **L1-escape:** With an open session, Esc on list returns to its canvas; with none it does nothing. Enter on a session enters canvas. Send Tab and Esc there and assert exact session bytes; only existing leader leaves canvas. No-match Esc clears, and dialog Esc closes only one level. Never exits app.
- **L1-navigation:** Vault and All alerts (#93) reachable solely by keys with Vault opening Credentials and Alerts opening All alerts (ruled O5); Enter opens intended screen. F1 follows approved lock/form policy from P1. Red: All alerts currently has only a nonfocusable click path. Both-size snapshots and viewport-contained hints; text contrast >=4.5.

## Ruled O3/O5 — destinations and filter exit

Vault opens Credentials; Alerts opens All alerts. n opens New session; a Add target is restricted to target.edit. / enters a list filter; Esc exits/clears it before the second Esc returns to open canvas (or does nothing without one). F1 opens Menu except on lock/login screens, where it does nothing. Editable-field keys remain text input.

## Steps

1. Add keys-only failing focus-chain, + New and All alerts tests; preserve hotfix Tab-within-chrome and terminal key-fidelity regressions.
2. Implement the table exactly, rail Up/Down/Enter and n, and ruled Credentials/All alerts destinations. Do not make canvas a Tab stop or intercept its Esc/Tab.
3. Implement list Esc canvas-return/no-open behavior and explicit no-match clearing; preserve caller focus across dialogs and sidebar persistence.
4. Run snapshots at 80x24 and 160x45, reversed traversal, PTY leakage assertions and hotfix regressions before serial handoff to P3.

## Gate and evidence

Before implementation, record the applicable ruling references and exact files/test function names in the orchestrator’s dispatch brief. Capture red assertion output on the hotfix baseline; implement; rerun the same tests green. The new test files above are planned names, not commands that can run before they exist. Use `env -u NO_COLOR python -m pytest <this phase’s test files> -p no:cacheprovider`, plus the named existing regression suites. Check actual test filenames before dispatch.

Gate: all phase assertions pass, both-size baselines reviewed, no new skips/xfails, no out-of-scope paths in `git diff --name-only`, and `make contract-check`, `make lint`, `make test` pass on the integrated branch with NO_COLOR unset. A dependency/setup failure is reported as unverified, never green. The phase report includes exact commands, output and red/green evidence; the orchestrator verifies it before the next merge. Stop every process started; no background command survives handoff.
