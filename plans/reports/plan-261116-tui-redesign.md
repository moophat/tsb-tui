# TUI redesign plan — planning lane report

**AI-Model:** gpt-6.1-sol  
**Date:** 2026-11-16  
**Status:** BLOCKED

## Delivered

Review-only [unit plan](../261116-1920-tui-redesign/plan.md) and six serial phase drafts, mapping all 13 ruled picks and ten visual rules to named layer-1 tests. Each phase states read/write scope, red behavior against the hotfix, two-size snapshots, keyboard/focus/contrast criteria and integration gates. The plan records draft/ruling differences and preserves the hotfix baseline. No phase is builder-ready until the OPEN register is resolved and the orchestrator reviews/red-teams it.

## Blocker and required decisions

Pick 10 requires truthful saved-login status for every target. Pinned TargetRecord and inventory-list expose no resolved credential status; available credential lists do not prove target applicability and mapping/grant writes are no read preview. Resolving credentials by trying session create would mutate state. Options: a separately owned public contract capability, operator-approved unknown-until-connect wording, or an explicitly narrower claim. Recommend the wording change for this TUI-only unit; it remains unapproved. No implementation is planned around an invented field.

Additional OPENs cover 28-column prose rows/multiple open-session numbers, list-key conflicts and modal F1 precedence, prompt style/overlap/history, responsive breakpoint and focus destinations, filter/paging/position/removal semantics, Nord’s orange accent and contrast scope, and add-target continuation. Recommendations are proposals only.

Pick 13 prerequisite checks passed: drive parses OSC 133; vendored engine exposes per-cell prompt metadata; the pinned inventory file and TargetRecord carry prompt_pattern. Python renderer integration is missing but local. TargetDefinition deliberately excludes prompt_pattern editing, so this unit uses the existing target-file field and introduces no editor or core change. The existing create-target form and target.edit permission path exist for pick 12.

## Evidence inspected

Read all three plan inputs in order, repository instructions and required project context; sibling program spine, decisions and TUI track; pinned architecture contract/open decisions and SF-CO protocols before schemas; relevant implementation and tests. Read GitHub issues #82–#93 with gh issue view. Inspected recent main history and hotfix history through 4155ed3, its three-dot diff and code overlaps. A read-only gpt-6.1-sol scout summarized bulk code inspection; its key credential/prompt findings were checked against the actual schema, protocol and engine header.

This checkout contains reports but no existing local unit-plan directories. Existing snapshot tests export SVG/content at three other sizes; committed visual comparison at 80x24 and 160x45 still needs implementation. #93 keyboard access to All alerts is outstanding in the inspected hotfix. Main #91 must survive hotfix integration.

## Verification and limits

- `make contract-check`: `contract-check: program/ is clean at c4c2b81bb7a1, v0.18.0` (exit 0).
- Document validator: checks seven plan files, six phase links, every pick and V1–V10, attribution, required phase sections and absence of absolute machine paths; PASS: 7 plan files; 6 phase links; 13 picks; 10 visual rules; attribution, phase sections, path and whitespace checks (exit 0).
- `git diff --check`: exit 0; new documents separately checked for trailing whitespace because untracked files are not included in that command.
- Scope: only this plan directory and this report written. Existing untracked .plan-inputs retained. No code, requirement source, ledger, other plan or submodule changed. No commit or push.

No application tests/build/lint were run: this is a document-only planning lane, with no code change and a design blocker. Predicted red/green requirements in phase files are not executed test evidence. FM01 real-terminal/vision checks and Tu’s golden-gallery approval were not performed. All tool commands were foreground and completed; no process remains from this lane.

## Amendment 1 incorporated — 2026-11-16

**AI-Model:** gpt-6.1-sol  
**Status:** DONE — amendment folded in; this section supersedes the original report’s global BLOCKED assessment. Implementation and runtime validation remain future work.

The plan now consumes the orchestrator’s existing O1–O8 rulings together with Amendment 1. P3 proceeds with no target login words; P3b alone waits on the separate login-hint contract release. No future contract shape was invented. P2 was replaced by serial P2a geometry/framing and P2b navigation, with the exact focus-chain table, canvas Tab/Esc fidelity, list Esc rules, rail keys, Credentials and All alerts destinations.

Incorporated the amendment’s 81-column word-rail breakpoint, exact Unicode/ASCII compact marks, last + New row, deterministic minimum HSL-lightness surface adjustment to 1.2:1 with per-theme hex report required from P1, 24-cell inner row budgets, exact empty/error copy and recovery keys, selectable last Add row and Enter/a path, ended regression/new-switch distinction, exact bold/━ versus ─ tab treatment, and literal name/host search without DNS. The older 100-column threshold and OPEN recommendations are superseded explicitly. P6 grades the amended release scope without treating deferred P3b as a skip or claiming final login-word acceptance complete.

Validation (all exit 0):

- Document checks: PASS: 9 plan files, 8 valid phase links, 13 picks, 10 visual rules, all Amendment 1 finding references; P2 split, P3 unblocked by O1, P3b deferred; attribution/path/whitespace checks.
- make contract-check: program/ is clean at c4c2b81bb7a1, v0.18.0.
- git diff --check: clean; untracked plan document whitespace checked separately.

Only plan/phase files and this new report section were written in this amendment pass. The old combined P2 file was removed after its content was split. No code, inputs, ledgers, other plans, commit or push. No application tests were run for this documentation-only amendment. The raw 15-finding red-team report is not in the supplied inputs; every finding reference and ruling supplied by Amendment 1 is mapped, without claiming an independent reread of the missing report. All foreground commands completed; no process remains from this lane.
