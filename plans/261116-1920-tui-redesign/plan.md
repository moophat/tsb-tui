# U7 — TUI redesign, layout B

**AI-Model:** gpt-6.1-sol  
**Date:** 2026-11-16  
**Status:** ruled 2026-11-16 19:4x (see Ruled); red-team next; not yet a builder authorization.  
**Inputs:** main `0414b1a`; hotfix inspected through `4155ed3`; program `v0.18.0` (`c4c2b81bb7a1`).

## Outcome and stop condition

After login, the session cockpit exposes Targets above sessions, with discoverable keyboard actions, readable words, frames and selection at both 80x24 and 160x45. New session searches inventory instead of asking for an id. Prompts are distinguishable using shell marks and the existing per-target pattern. Every ruled pick and visual rule has an executable acceptance criterion below.

**Amendment 1 supersedes the original global O1 stop.** P3 proceeds without any target login words. P3b alone waits for the login-hint contract release, tracked by tsb-core `plans/reports/design-261116-target-login-hint.md`. This plan does not design that capability, invent its schema or bump program/. O2–O8 are resolved by the orchestrator’s Ruled section and Amendment 1; only P3b waits on the separate contract release. Orchestrator review and red-team remain prerequisites; no implementation was performed.

Verification for this planning lane: prove the existing contract pin; check all 13 picks and ten visual rules map to phases/tests; check phase references and scope; show only allowed files changed. Future red/green claims below are required builder evidence, not tests run in this lane.

## Task breakdown and checkpoint

- [x] Read the three inputs in order; distinguish the ruling from the earlier draft.
- [x] Inspect code, pinned protocols and schemas, issues #82–#93, and hotfix history/diff.
- [x] Verify both prompt inputs exist without a core or contract change.
- [x] Record the blocking login-status seam and unresolved design options.
- [x] Write the acceptance map, serial phase boundaries and handoff report.
- [ ] Orchestrator reviews the amended plan and authorizes builders after the hotfix merges.

Done: evidence and conditional decomposition. Verified: contract pin and document checks in the report. Left: P3b contract dependency, orchestrator review/dispatch, implementation and runtime UX proof.

## Authority and ruled section

The authority is `.plan-inputs/picks-ruled.md`, not the research draft. Its status says: “No comment or change came, so every default below is the ruling.” The following quotations preserve the actual ruled values:

| Pick | Ruled quotation | Phase / grading test |
|---|---|---|
| 1 | “B” | P2a / L1-layout-B |
| 2 | “Nord; orange stays selectable” | P1 / L1-theme |
| 3 | “Keep Ctrl+\” | P1 / L1-keys |
| 4 | “Plain keys in every list: / filter, Enter open, n new, ? keys, no leader needed” — “Yes” | P3, P4 / L1-list-keys |
| 5 | “F1 opens the Menu from anywhere” — “Yes” | P1 / L1-F1 |
| 6 | “Key hints always on screen, the key styled apart, written Ctrl+\ never ^\” — “Yes” | P1, P6 / L1-hints |
| 7 | “The left bar: words (Sessions, Targets, Vault, Alerts, Menu), 12 columns wide” — “Yes” | P2a, P2b / L1-rail |
| 8 | “Targets on top, your sessions under” — “Targets first” | P3 / L1-targets-first |
| 9 | “Ended sessions out of every default list, behind a ‘show ended’ switch” — “Yes” | P3 / L1-ended |
| 10 | “As written” | P3 / L1-row-grammar; P3b / L1-login-hint |
| 11 | “Every list shows its position (14/230), and typing filters by name, host or IP” — “Yes” | P3, P4 / L1-filter-position |
| 12 | “New session searches the same list; no match lets a login with target rights add the target, then opens it” — “Yes” | P4 / L1-new-target |
| 13 | “(a) and (b)” | P5 / L1-prompts |

Pick 10's final grammar is literal, with its login words deferred by Amendment 1 to P3b: targets show “login saved” or “no saved login: type it at connect”; targets belong to nobody and show the number of their open session. Sessions show “live”, “detached”, “unreachable”, or the end word “killed”/“terminated”; “yours” or the owner's name; their number in this window; and “you type”, “<name> types” or “nobody typing”. A mark supplements a word, never replaces it. Colour alone never carries meaning.

The ruling's order is “needs you, then open here, then yours (live before detached), then joinable”. Refresh preserves the highlighted entity. Ended rows appear only with show-ended enabled. The Amendment 1 row budgets below govern compact rendering; P3 omits login words entirely until P3b. Do not substitute “saved”, “none” or a glyph for the deferred words.


### Amendment 1 — orchestrator rulings (2026-11-16)

These rulings supersede the earlier OPEN recommendations where stated. The amendment identifies 15 accepted red-team findings by the grouped references below; the raw red-team report was not supplied in this checkout.

| Finding | Binding rule | Phase / test |
|---|---|---|
| 1.1 | P3 never stops at O1: no target login words until P3b, waiting on the separate login-hint contract release. | P3 / L1-no-login-words; P3b / L1-login-hint |
| 1.2 | Rail words at 81 columns and wider; marks at 80 columns and narrower. | P2a / L1-rail-breakpoint |
| 1.3 | Every theme surface is its bg moved in HSL lightness, lighter for dark themes and darker for light themes, by the smallest step reaching 1.2:1. P1 report lists resulting hex values per theme. | P1 / L1-surfaces |
| 1.4 | + New is the last rail row; Enter or n opens New session. | P2b / L1-rail-new |
| 2.1 | Rail → Targets list → Sessions list → Rail; Shift+Tab reverses. Rail is one stop with Up/Down and Enter. Canvas entered by Enter on a session row or existing leader binding; only existing leader binding leaves it. Canvas Tab/Esc belong to attached session. Esc in a list returns to open canvas, otherwise does nothing. | P2b / L1-focus, L1-escape |
| 2.2, 3.3 | Exact Unicode/ASCII compact marks: Sessions ▸/>; Targets ▦/#; Vault ◆/*; Alerts !/!; Menu ≡/=; + New +/+. | P2a, P2b / L1-compact-marks |
| 2.3 | 28-column panel has 24 inner columns. Target: two lines, name (host if unnamed, terminal ellipsis when name longer), then host · open N,M, open numbers only when any. Session: up to three lines: N name; state · owner; typer words. Never cut a word. At 160 columns every row is one line. | P3 / L1-row-budget |
| 2.4 | Exact empty/error copy and keys below; e toggles show ended within session lists. | P3 / L1-empty, L1-ended |
| 2.5 | New session no-match for target.edit: selectable last row + Add '<query>' as a target; Enter or a opens existing target form. | P4 / L1-new-target |
| 3.1 | Ended default exclusion is hotfix regression coverage; newly failing checks are show-ended switch and end words. | P3 / L1-ended |
| 3.2 | Active tab label bold with ━ under it; inactive labels with ─ under them. Assert characters under each label. | P2a / L1-tabs |
| 4.1 | P2 splits serially into P2a geometry/framing then P2b focus/navigation. | P2a → P2b |
| 5.1 | Search literal name and host field text, including IP text; no DNS resolution. | P3, P4 / L1-filter-position |

Exact copy (plain text, with the actual query substituted):

| State | Copy / action |
|---|---|
| Targets empty, target.edit holder | No targets yet · a add one |
| Targets empty, other login | No targets you can see |
| No match | No target matches '<query>' · Esc clears |
| Targets loading | Loading targets… |
| Sessions loading | Loading sessions… |
| Targets error | Could not load targets · r retry |
| Sessions error | Could not load sessions · r retry |
| Sessions empty | No live sessions · n new session |
| Only ended sessions exist | No live sessions · e show ended |

Esc clears the no-match query; otherwise list Esc follows the canvas-return rule. This specific no-match recovery takes precedence over the general list Esc rule. An attached canvas receives Tab and Esc unchanged.

## Acceptance: visual rules

Each test identifier names a required test family, not an existing function. Pilot uses keys, never clicks or direct action calls to prove reachability. Each family captures reviewed SVG baselines at **80x24 and 160x45**, waits on state rather than sleeping, and uses published-contract fixtures/test doubles. Add semantic and geometry assertions so accepting a new screenshot cannot conceal a regression.

| Rule | Measurable acceptance | Phase / test |
|---|---|---|
| V1 Focus and highlight | Exactly one current pane has the focus border; selection background spans its entire content row; chrome Tab/Shift+Tab follow Amendment 1’s chain, while canvas Tab/Esc reach the session unchanged. | P2b, P3 / L1-focus |
| V2 Whole frames and padding | Every panel perimeter has all four joined corners, continuous sides and one blank content cell next to each border; no orphan segments. Assert rendered cells, not CSS declarations. | P2a, P6 / L1-frames |
| V3 Chrome versus session | Every shipped theme surface/bg contrast is >=1.2:1 using the ruled minimum HSL-lightness movement; assert direction and resulting hex per theme. | P1, P2a / L1-surfaces |
| V4 Tabs | Every tab remains accessible at both widths; active label is bold over ━, inactive labels over ─; assert the exact underline cells in monochrome. | P2a / L1-tabs |
| V5 Hint spacing | Each key and label has at least one blank cell between them; key style differs visibly from label; current-screen hints remain inside the viewport. | P1, P6 / L1-hints |
| V6 Menu | One framed column; equal key/name column starts; matching substrings preserve all original spaces; typing narrows matches; first selection is enabled; menu mounts on the next completed UI refresh without waiting for network. Esc restores caller focus. | P1 / L1-menu |
| V7 Empty lists | Assert Amendment 1’s exact copy for empty/loading/error/no-match/permission states, and exercise a, r, n, e and Esc where advertised; no repeated zero groups. | P3, P4, P6 / L1-empty |
| V8 Contrast | Computed effective text/background contrast >=4.5:1 for every chrome text role in each shipped theme, including selected, disabled, hints, tabs, errors and prompt decorations. Target-supplied ANSI colours remain faithful, as ruled O7. | P1, P5, P6 / L1-contrast |
| V9 Minimum size | At 80x24 and 160x45 every action, footer and focus stop is reachable, with no clipped controls or unintended horizontal scroll; assert widget rectangles and rendered text, plus scroll-to-selection at row 8,000. | P2a–P6 / L1-geometry |
| V10 Esc | No-match Esc clears query; other list Esc returns to open canvas or does nothing without one. Dialog Esc backs one level, never exits. Attached-canvas Esc is target input. | P1–P6 / L1-escape |

## Evidence and compatibility baseline

The checkout has only `plans/reports/`, no existing local unit plan or phase-file precedent. This directory uses the requested `plan.md` plus `phase-NN-*.md` shape; report style retains attribution, evidence and limitations.

The hotfix branch is not a descendant of current main: its history branches from `fd0dd22`, while main adds #91. Inspect both during integration; do not replace main with the hotfix. Hotfix changes `MenuPalette`/`MenuProvider`, leader dispatch, sidebar scrolling and New session focus, terminal sidebar lifetime, ended filtering and create-size handling. Preserve its regression tests: slow second leader keys, Enter opens or explains ended, n/New reachable, menu search/highlight/spacing, creation with auto-size off, sidebar staying open, and Tab never leaking to the session. These are baseline protections, not newly red redesign tests. #93's All alerts reachability is not present in the inspected hotfix diff and remains P2b work. #91 is a drive change already on main, not a redesign issue.

Code surfaces: `app.py` owns MenuPalette and chrome CSS; `widgets/rail.py` and `widgets/sidebar.py` paint custom rows; `screens/terminal.py` coordinates workspace/fleet; `screens/fleet_view.py` owns full-view lists; `model/fleet.py` owns fleet projection; `screens/new_session.py` still takes a target id; `screens/target_form.py` already implements target creation. Theme defaults live in `config_default.toml` and `config.py`, palettes in `themes/`, role resolution in `theme.py`, marks in `glyphs.py`.

Prompt prerequisite verified: `drive/shell_marks.py:ShellMarks` parses split OSC 133 sequences; `drive/prompt_pattern.py:compile_pattern` implements whole-row I-Regexp matching with trailing spaces trimmed by its caller; `drive/commands.py` loads target patterns. The pinned `inventory-target.schema.json#/properties/prompt_pattern` and OpenAPI `TargetRecord` serve the optional field. `TargetDefinition` explicitly excludes editing it and preserves it on replacement. Use existing target-file configuration; adding a prompt-pattern editor is outside scope. `vendor/libghostty-vt/include/ghostty/vt/screen.h` exposes `GHOSTTY_CELL_DATA_SEMANTIC_CONTENT` (9), including PROMPT (2); Python `term/backend.py:Cell` does not yet expose that metadata. This is local rendering work; no vendor edit, private broker access or new wire format is required. ShellMarks' at-prompt boolean alone does not locate prompt cells.

## Decision register after Amendment 1

O1 is an external release dependency owned by P3b, never a P3 blocker. O2–O8 are ruled below, with Amendment 1 taking precedence over older thresholds and budgets. Their earlier alternatives are superseded, not active OPEN choices. No other OPEN design decision is identified by this amendment; a builder discovering one stops and reports options rather than inventing behavior.

## Ruled (the orchestrator, 2026-11-16 19:4x, DEFAULTs, veto by line)

- **O1: A, as its own track; the TUI does not wait for it.** The corpus already expects a surface to know it: `Frontend` §3.2 lists "a target with no resolving credential is listed, and connectable only with a login typed at connect (`Storage` §10.1a)". The missing piece is a contract addition (a caller-relative login hint on the target record), designed, red-teamed and released through tsb-program on the operator's go; tsb-core implements it; a later TUI phase (P3b) shows the words. Until that release is pinned, a target row shows no login words at all (never a guess, never a probe by session create), and pick 10's other words ship in P3 as written. Pick 10 is not narrowed.
- **O2: wrapped rows.** Amendment 1 fixes the 28-column/24-inner budget: targets two lines (name/host fallback then host · open N,M), sessions up to three lines (N name; state · owner; typer words); at 160x45 one line. Every open session number of a target is shown. P3 writes the exact column and line budget and the long-name rule (ellipsis at the end of the name, never inside a word of the state), and asserts it at 80x24 and 160x45.
- **O3: `n` is New session in every inventory and session list; Add target is `a`** (shown only to a login holding `target.edit`). Typing in an editable field stays typing. `/` enters the filter; Esc leaves it and clears it; a second Esc goes back one level. F1 opens the Menu from every screen except the lock and login screens, where it does nothing.
- **O4: as recommended.** The prompt is bold and underlined, never recoloured; OSC 133 marks win where the shell prints them, the target's prompt pattern is the fallback and is matched on the cursor row only; no historical rows are marked by guess; an invalid pattern is ignored and named once in the session's status line.
- **O5: as recommended.** The rail is 12 columns with words from 81 columns wide, 4 with marks at 80 or below (Amendment 1 supersedes the earlier 100-column threshold); Vault opens Credentials; Alerts opens All alerts. P2 writes the focus chain as a table and asserts it.
- **O6: as recommended.** The position counts within the filtered results, after every published page is loaded; the highlight follows the same entity; when it disappears, the next surviving row; name, host and IP search is local.
- **O7: Nord with a non-orange chrome accent** (Nord frost `#88c0d0`); orange stays selectable as its own theme. The side panels' background and the session background differ by a contrast ratio of at least 1.2:1. The 4.5:1 text rule applies to the chrome and every decoration TSB adds; the target's own colours are shown as received.
- **O8: as recommended.** No match opens the existing target form with nothing inferred; after a successful save, New session continues with the record the save returned; Cancel creates nothing; a refusal is shown and never retried.

With these rulings the plan is ready for a cross-family red-team; P1 dispatches after tsb-tui #94 merges and the red-team's findings are ruled.

## Differences from the research draft

The ruled 13-pick numbering replaces the draft’s 14 picks. B is selected; the alternative inventory-only A is discarded. The draft’s leader banner and 300→1000 ms timer proposal is superseded by the hotfix’s immediate menu and indefinite second-key dispatch; no redesign timer change. The draft’s remembered sidebar order is not ruled: Targets stay first. The draft leaves prompt treatment undecided; ruling selects both OSC 133 and target pattern, excluding cursor-line tint. Its sketch shows session ids and abbreviated “cred”/“none”; literal ruled words and no user-facing ids supersede that sketch. F1 is Menu, not a competing full-keymap binding. The draft’s proposed stale-row wording is input only; Amendment 1 now rules the tab characters and exact empty-state copy.

## Phases and merge order

The hotfix must land first alongside main #91. Recheck its actual merged diff and green gates; then orchestrator checks incorporation of the accepted red-team findings before dispatch. Merge **P1 → P2a → P2b → P3 → P4 → P5 → P6**; P3b is a separate follow-up after P3 and its contract release, serialized against any phase touching the same files, never a prerequisite for P3/P4/P5/P6, one PR at a time. Each boundary is at most one focused builder session; split further rather than exceed that boundary. All phases touching app, terminal, sidebar, theme or snapshot fixtures are serial. No phase silently expands its file list; send any necessary expansion back to the orchestrator.

1. [P1 — theme, hints and menu entry](phase-01-theme-and-keys.md)
2. [P2a — geometry and framing](phase-02a-geometry.md)
3. [P2b — focus and navigation](phase-02b-navigation.md)
4. [P3 — inventory and session rows](phase-03-lists.md)
5. [P4 — searchable New session](phase-04-new-session.md)
6. [P5 — prompt decoration](phase-05-prompts.md)
7. [P6 — complete screen gallery and CI gate](phase-06-ux-gate.md)
8. [P3b — target login hints, waiting for contract](phase-03b-login-hints.md)

## Non-goals and risks

No core/API/contract changes in this unit; no drive command or send-gate change; no recording, continuity, restart, handshake or wire-format redesign; no screen-lock or vault-lock weakening; no cursor-line tint (pick 13c); no inventory imports/private targets; no deployment, live broker tests, commit or push in this lane. A create still carries window size with auto-size disabled. Choice of credential, connection and session remains explicit.

Risks: incomplete pages causing false no-match; stale asynchronous results replacing a newer query; 8,000-row refresh moving selection; frame/padding starving 80x24; keystrokes leaking into a session; replacing hotfix behavior; template snapshots concealing functional regressions; prompt metadata cache invalidation and wide-cell boundaries. Each has an assertion in its owning phase.

Existing `tests/test_snapshots.py` exports text/SVG at 65x30, 120x40 and 200x60 and asserts content; it does not yet provide the requested committed two-size visual-diff gate. P6 must add baseline comparison, not count exported files as visual regression coverage.

Layer 1 belongs to this repo's CI: keys-only login/open/new/leader/sidebar/full-view/Esc flows, every-screen two-size SVGs, focus chain and contrast. Inventory every Screen subclass and every material state (empty, populated, loading, error, permission-limited, stale, ended, locked); no skipped screen is called complete. Layers 2/3 belong to FleetManager01: released binary in a real terminal for PTY, resize, reconnect and broker behavior, then severity-scored gallery review. Tu supplies taste approval of two or three golden references; which vision reviewer to use remains FM01's decision. Headless green does not claim those release checks passed.
