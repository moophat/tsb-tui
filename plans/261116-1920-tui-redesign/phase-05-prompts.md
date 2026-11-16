# Phase 05 — Prompt decoration from shell marks and target pattern

**AI-Model:** gpt-6.1-sol  
**Status:** Amended review draft — orchestrator dispatch after hotfix; rulings incorporated.  
**Depends on:** integrated hotfix and preceding phase; ruled O4/O7 in [plan.md](plan.md).  
**Next:** P6; serial merge only.

## Scope and authority

One focused builder session under the recorded rulings. Implement only this phase’s acceptance. If the plan leaves any design decision unspecified, STOP and report the options; never choose one yourself. The orchestrator owns plans, rulings and ledgers; builders write only approved code/tests and their own report. Do not edit program/, .plan-inputs/, vendor/ or these phase files.

## Read before writing

Repository-relative paths under tsb_tui/ unless explicitly qualified: term/ghostty.py; term/backend.py (Cell interning/cache and grid reads); widgets/canvas.py (paint grouping and highlight overlay); screens/terminal.py attachment lifecycle; drive/shell_marks.py; drive/prompt_pattern.py; drive/commands.py prompt loading; vendor include/ghostty/vt/screen.h; pinned inventory-target schema and OpenAPI TargetRecord.
Read exports, callers, tests and shared utilities at the actual merged baseline. A missing or renamed file is a context check, not permission to invent an alternate interface.

## Files allowed to write

tsb_tui/term/ghostty.py; tsb_tui/term/backend.py; tsb_tui/widgets/canvas.py; tsb_tui/screens/terminal.py; tsb_tui/theme.py; tests/test_redesign_prompts.py; tests/test_term_engine.py; this phase’s snapshot baselines.

## Tests first and measurable targets

- **L1-prompts-osc:** Feed real-engine split OSC 133 A/B/C/D sequences terminated by BEL and ST; assert exact PROMPT-tagged glyph cells get approved emphasis, input/output do not. Cover no marks, multi-line/wide glyphs, scrolling, reset, resize and attach prefix. Red: Python Cell and canvas discard semantic content.
- **L1-prompts-pattern:** Attach to a published target record with optional prompt_pattern; use existing compile_pattern semantics and approved O4 paint policy. Valid match/nonmatch, unusable pattern, absent field, stale target response and OSC overlap give exact approved results. Pattern configuration stays in target file; no target-form field. Red: interactive pane never reads/applies pattern.
- **L1-contrast / L1-geometry / L1-focus:** Both-size snapshots for marked shell and unmarked network-device fixtures; approved styles meet contrast without altering received bytes or nonprompt ANSI colours. Pilot switches attachments and opens/closes chrome; verify decoration remains attachment-local, cursor/search/copy precedence follows O4. No universal cursor-line tint.

Every relevant Pilot case uses keys and captures reviewed SVGs at 80x24 and 160x45. Assert focus, rendered text/style and geometry separately from snapshots. Run against fixtures/test doubles, never a workstation broker or sshd. Preserve green hotfix regressions; the phase’s stated new behavior must fail on hotfix, not merely on missing test infrastructure.

## Ruled O4/O7 — exact decoration

Prompt cells are bold and underlined, never recoloured. OSC 133 wins where emitted; otherwise use the target pattern on the cursor row only. No guessed historical prompt marks. Invalid pattern is ignored and named once in that session’s status line. Assert no foreground/background change and correct bold/underline, pattern fallback, OSC precedence and once-only invalid-pattern status. The 4.5 rule applies to chrome and TSB-added decorations, never adjustment of received target colours.

## Steps

1. Use ruled O4/O7 prompt style and precedence below. Add real-engine red rendering tests before implementation.
2. Bind existing semantic metadata without changing vendor headers, engine pin or core. Include semantic kind in any cell/style cache identity that affects painting.
3. Reuse the I-Regexp implementation read-only; do not duplicate regex semantics or alter drive’s send gate. Use the existing public get-target path and approved attachment lifecycle.
4. Apply decoration in the client renderer only; retain input bytes, grid size, copy text, recording and target ANSI data.
5. Run engine, canvas, faithful-colour, drive shell-mark and prompt-matcher regressions; inspect actual cache invalidation after output/scroll/resize.

## Gate and evidence

Before implementation, record the applicable ruling references and exact files/test function names in the orchestrator’s dispatch brief. Capture red assertion output on the hotfix baseline; implement; rerun the same tests green. The new test files above are planned names, not commands that can run before they exist. Use `env -u NO_COLOR python -m pytest <this phase’s test files> -p no:cacheprovider`, plus the named existing regression suites. Check actual test filenames before dispatch.

Gate: all phase assertions pass, both-size baselines reviewed, no new skips/xfails, no out-of-scope paths in `git diff --name-only`, and `make contract-check`, `make lint`, `make test` pass on the integrated branch with NO_COLOR unset. A dependency/setup failure is reported as unverified, never green. The phase report includes exact commands, output and red/green evidence; the orchestrator verifies it before the next merge. Stop every process started; no background command survives handoff.
