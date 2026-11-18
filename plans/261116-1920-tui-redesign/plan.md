# U7 — TUI redesign, layout B

**AI-Model:** gpt-6.1-sol  
**Date:** 2026-11-16  
**Status:** ruled 2026-11-16 19:4x (see Ruled); amended 2026-11-18 (Amendment 2, the design review); P1 merged and released in v0.8.0; P2a next.  
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

## Amendment 2 — the 11-18 design review (the orchestrator, 2026-11-18 13:4x, DEFAULTs, veto by line)

On the operator's request of 2026-11-18 13:10, an independent pass with the UX skill over the ruled picks, this plan and what P1 built (tsb-core `plans/reports/review-261118-tui-redesign-picks.md`: 17 refines, 1 veto, 4 questions, 12 kept). Every refine was taken. This amendment supersedes the plan's text where they differ, phase by phase; each phase's dispatch brief quotes its rows. The research quotes behind each row are in the review.

**Done in P1 (rounds 2 and 3, tsb-tui #98, released in v0.8.0):** the veto (the protected hints are "F1 Menu", key first, and "? keys" wherever they apply, plus Take back and Scroll; the name leaves whole before a protected hint; below 80 columns Scroll drops first, then keys, then Take back, the Menu last); Nord's `steel` apart in hue from its frost accent, every shipped theme tested for accent and steel apart in hue and at 4.5:1; the hint drop order an explicit priority, a screen's primary action dropping last among ordinary hints.

| Phase | Rule | Acceptance (test family) |
|---|---|---|
| P2a | The session frame's top border carries the front session's facts: its number, target name, state, owner, who types and "recorded". The bottom row carries keys only. | L1-frame-title: facts in the frame's top border line, none on the bottom row, at 80x24 and 160x45 |
| P2a | The rail's Alerts row is shown only to a login holding `broker.health` (Frontend §3.2). | L1-rail-alerts: present with the permission, absent without it |
| P2a | The side panel's Sessions section shows every session open in this window: its height is its rows, at least 3 and at most half the panel; with none, its one-line empty state. Targets take the rest and scroll inside their section. | L1-panel-split: section heights at both sizes with 0, 1, 5 and 20 open sessions |
| P2a | The focused pane, the session frame included, has a heavy border (┏━┓) and every other pane a light one (┌─┐), in every theme and in monochrome. | L1-focus-border: border characters per pane, NO_COLOR set and unset |
| P2a | Padding is one blank column inside the left and right borders; section titles sit in the top border line ("┌─ Targets · 14/230 ─"); no blank padding rows. This replaces V2's "one blank content cell next to each border" for the top and bottom. | L1-frames, amended |
| P2b | The rail lists, under Sessions, the sessions open in this window, one row each: number and target name ("1 core-rtr-01", cut with an ellipsis at 12 columns; "1•" at 80 columns and below), with a mark when a background session has printed since it was last shown. The Sessions row carries the needs-you count ("Sessions ▲1"). | L1-rail-sessions; L1-rail-activity |
| P2b | After login the keyboard lands in the Targets filter. | L1-first-focus |
| P2b | Enter on the rail's Sessions or Targets row moves the keyboard to that list in the side panel, opening the panel if it is closed. | L1-rail-enter |
| P2b | One word, "Vault": the Menu item is "Vault…" (saved logins, with Devices and Lock now beside it); "Fleet" leaves every label (the side panel is "Targets and sessions", the full view is named by its tab); the old names stay as Menu search aliases. | L1-words: no rendered "Fleet" or "Credentials…"; the aliases find their items |
| P2b | An unfocused list keeps its selection as a dim bar with ▸. | L1-focus, amended |
| P2b | One letter, one meaning between the lists and the leader: `n` is New session in both (`c` stays an unadvertised alias); next and previous session move to Ctrl+\ then → and ←; Delete target moves to `D`. Drive's documented key sequences are untouched. | L1-one-meaning: no letter means two things between the list keys and the leader keys; drive's GUIDE sequences unchanged |
| P2b | Ctrl+\ then F1 sends F1 to the session. | L1-F1-passthrough |
| P2b | A one-line notice in the session frame when the keyboard moves to you ("you type now: ana left"; Frontend §9). | L1-token-notice |
| P3 | A target row shows its type word when it is not ssh ("telnet", "serial"); its label falls back name, then host, then the type and the target's id (the id shown, never asked for). Targets with a session open here come first, then the rest in natural name order (rtr-2 before rtr-10). | L1-row-grammar, L1-target-order |
| P3 | A session's end reads with its reason ("killed · idle", "killed by the owner", "terminated · the device closed it", "terminated · connection lost"), never a lone red ×; "nobody types" everywhere; the start time ("since 09:12") when two rows would read alike; "recorded" in the session frame's title, not on every row. | L1-row-grammar, L1-ended |
| P3 | The filter also matches group and tag text (still local, no DNS). The title says what it counts ("Targets · 3/12 of 230 · 'rtr'"); while pages load, "Targets · 14/500… loading", never a false total. | L1-filter-position, amended |
| P3 | A details key (`i`) shows a target's details at any width; no detail and no link needs 160 columns. | L1-details |
| P3 | Other logins' live sessions on a target show on its row ("ana on it"), from the session list the TUI already reads. | L1-others-on-target |
| P3 | "No targets you can see · ask an admin for access"; the no-session card ends with "F1 menu". | L1-empty, amended |
| P4 | Enter on a target creates the session at once when there is one way to open it, and shows a choice only where the contract asks for one: a live connection exists (Contract §15.3), the core answers `credential_choice_required` or `connection_choice_required`, or a gate applies (recording notice, first-use host key, a typed login). | L1-open-direct |
| P4 | A no-match add opens the target form with the typed text in the address field when it reads as an IP address or host name; type, port, name and login stay empty (the operator's own text, never an inference; refines O8). For a login without target rights: "No target matches '<query>' · ask an admin to add it · Esc clears". | L1-new-target, amended |
| P4 | One table of exact refusal copy for opening a session (`target_connect_failed`, `target_auth_failed`, `hostkey_changed`, `no_resolving_credential`, `vault_locked`, `org_credential_locked`, `mapped_credential_missing`, `device_not_trusted`, `target_not_found`, `inventory_unavailable`): what happened, why, and one recovery key each, never a retry; "no such target" and "a target id is required" retired. | L1-open-refusals |
| P5 | A prompt row that matched while the cursor sat on it keeps its mark as it scrolls up; rows from before this attach stay unmarked. | L1-prompts, amended |
| P6 | A monochrome and 16-colour gallery pass: the selected row, the focused pane, the active tab and who types each stay identifiable by shape or words with NO_COLOR set. | L1-monochrome |

**Questions to the operator, each with a default applied when its phase dispatches** (veto by line before then):

1. Prompt marks on network gear that prints no shell marks and whose target has no prompt pattern: DEFAULT a built-in device-prompt pattern (a device name followed by `>`, `#`, `]` or `$` where the cursor waits), used only where the target has no pattern of its own, switchable off (P5).
2. Grouping the target list: DEFAULT one list with the group shown on each row, typing matches the group; folded groups may come later as a switch (P3).
3. Enter on a device you already have a session on: DEFAULT ask, with "go to session 1" listed first and highlighted, then "another session" and "new connection" (P4).
4. Recent devices: DEFAULT a "Recent" section at the top of Targets, built from your own recent sessions, nothing stored (P3).

Unverified and noted: Ctrl+\ on a UK Windows keyboard; GNOME Terminal's own F1 binding; whether the core states the vault's lock state without a connect (the rail's vault state shows it only where the core reports it).

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
