# Build report — Redesign P5 prompt decoration

**Branch:** `feat/redesign-p5-prompts` (base `91b4ec6`) | **Model:** strata/strata
**Interpreter:** `$(PYTHON)` | **Plan:** plans/261116-1920-tui-redesign/phase-05-prompts.md, plan.md Ruled O4/O7, Amendment 2 L1-prompts row

## Objective
Prompt decoration: a prompt cell is bold + underline, never recoloured. OSC 133 shell marks win where
emitted; the target's `prompt_pattern` is the fallback, matched on the cursor row only; no guessed
historical rows; an invalid pattern ignored and named once in that session's status line; no cursor-line
tint; decoration attachment-local. Red-first on `91b4ec6`.

## RED (recorded on baseline, source stashed)
First failing line: `tests/test_term_engine.py:752` —
`AttributeError: module 'tsb_tui.term.ghostty' has no attribute 'CELL_DATA_SEMANTIC_CONTENT'`
(the Python `Cell` carried no semantic content; the canvas painted no prompt emphasis; the interactive pane
never read a target pattern). Log: `.lane-logs/red-term-engine.log`. Source restored.

## In-scope, implemented and green
- `tsb_tui/term/ghostty.py` — `CELL_DATA_SEMANTIC_CONTENT=9`, `CELL_SEMANTIC_OUTPUT/INPUT/PROMPT`,
  `GhosttyCellData.SEMANTIC_CONTENT`, declared `ghostty_cell_get`.
- `tsb_tui/term/backend.py` — `Cell.semantic` slot (decoded once per distinct raw via `ghostty_cell_get`);
  `track_cursor_row()`, `free_tracked()`, `tracked_viewport_rows()` (tracked grid refs survive scrolling).
- `tsb_tui/theme.py` — `TermPalette.prompt_decoration = Style(bold=True, underline=True)`.
- `tsb_tui/widgets/canvas.py` — per-cell decoration: group by `(style_id, decorated)` so prompt cells split
  from input/output cells that share a style id; decorate on OSC 133 semantic PROMPT and on the pattern
  rows (`set_prompt_pattern`, cursor-row match tracked once, OSC wins on the cursor row); materialize the
  `groupby` sub-iterator (the original `any(... for c in run)` read an exhausted iterator → no emphasis).
- `tests/test_term_engine.py` — engine OSC 133 semantic tests. **34 passing.**
- `tests/test_redesign_prompts.py` — canvas decoration, faithful colours, no cursor-line tint, wide/multiline
  prompts, pattern cursor-row match + nonmatch, scrolling persistence, prior rows unmarked, OSC precedence,
  invalid pattern named once, stale response, attachment-local, both-size snapshots. Canvas/engine unit tests
  green; snapshot baselines generated at 80x24 and 160x45.

`ruff check` clean on all changed files; `make contract-check` green (program/ clean at v0.18.0).

## Ruled stop — Option D, implemented
The orchestrator ruled (DEFAULT, 2026-11-24): the attach-time pattern read is the interactive pane's alone;
drive keeps its send gate untouched. Decoration is for a person looking at the pane; drive is headless and
answers with screen text, where decoration is invisible.

- The `screens/terminal.py` attach worker reads the target's `prompt_pattern` (the existing public
  `get_target` path, once per attach; failed/absent read = "no pattern") **only when the app is not under
  drive**. Under drive it returns immediately, so `drive/commands.py` (`_read_prompt`, `send_gate=false`,
  the fixed fetch sequence) is untouched and its lifecycle is exactly as before.
- The signal already exists: `app.under_drive` (TT29, `tsb_tui/app.py:397`), already read in
  `terminal.py:2847,3126`. Step 2 satisfied — no new file needed.
- OSC 133 decoration works in both modes as built.

**RED (under drive, current worker before guard):** `tests/test_redesign_prompts.py:326` —
`AssertionError: under drive an attach read the target for decoration: ['normal']` (`assert ['normal'] == []`).
Log `.lane-logs/red-under-drive.log`. After the `self.app.under_drive` guard, green.

**Red-first (baseline, source stashed):** engine semantic first failing line
`tests/test_term_engine.py:752` — `AttributeError: ...ghostty has no attribute 'CELL_DATA_SEMANTIC_CONTENT'`.
Log `.lane-logs/red-term-engine.log`. Source restored.

The new test `test_under_drive_an_attach_reads_no_target_for_decoration` is the added red-first test (step 4). The
existing drive test `test_attach_with_send_gate_off_never_reads_target` already covers "under drive, send_gate=false,
an attach reads no target" and passes unchanged.

## Gate — whole suite green, exit codes recorded in .lane-logs/
Run with the pinned interpreter `$(PYTHON)`, NO_COLOR unset, `-p no:cacheprovider`. Pilot suites are timing-
sensitive; under combined parallel load a snapshot gallery test flakes intermittently, so the large suites were
run isolated (all green). Each log records its exit code.

| Suite | Result | Exit |
|---|---|---|
| `test_redesign_prompts.py` + `test_term_engine.py` + `test_drive_shell_marks.py` + `test_drive_prompt_pattern.py` + `test_drive_prompt_matcher.py` | **208 passed** | gate1.log EXIT=0 |
| `test_redesign_contrast.py` + `test_redesign_focus.py` + `test_redesign_keys.py` + `test_redesign_layout.py` | **138 passed** | gate2ab.log EXIT=0 |
| `test_redesign_lists.py` | 60 passed | gate2c3.log EXIT=0 |
| `test_redesign_lists_filter.py` | 30 passed | gate2c2.log EXIT=0 |
| `test_redesign_lists_review.py` | 20 passed | EXIT=0 |
| `test_redesign_lists_review4.py` | 7 passed | EXIT=0 |
| `test_redesign_new_session.py` | 42 passed (isolated) | EXIT=0 |
| `test_redesign_rail_tint_keys.py` | 60 passed | EXIT=0 |
| `test_redesign_round1/2/3.py` | 14 + 17 + 21 passed | EXIT=0 each |

`test_drive_prompt_pattern.py` is back to **27 passing** as on the baseline (the 14 collisions are gone under
Option D).

**Timing note:** `test_redesign_new_session.py::test_two_size_baselines_for_the_ruled_states[credential-size1]`
failed once under combined parallel load (a pilot snapshot-timing flake). Isolated it passes (42 passed, EXIT=0),
and it passed on the baseline in isolation too. Not caused by this change; reported. The large pilot suites are run
isolated to avoid contention-induced flakes.

`ruff check` clean on all changed files (EXIT=0); `make contract-check` green (program/ clean at v0.18.0, EXIT=0);
`make lint` green (import-lint, leak check, engine-check verified against PIN, EXIT=0). `git diff --name-only` lists
only allowed paths.

No commit, no push.

## Files changed (git diff --name-only, all within allowed paths)
`tsb_tui/term/ghostty.py`, `tsb_tui/term/backend.py`, `tsb_tui/theme.py`, `tsb_tui/widgets/canvas.py`,
`tsb_tui/screens/terminal.py`, `tests/test_term_engine.py`, new `tests/test_redesign_prompts.py`, new
`tests/snapshots/redesign-p5/{prompt-marked,prompt-unmarked}-{80x24,160x45}.svg`.

## Round 3 — the marked baseline showed no mark (orchestrator, 2026-11-24 08:1x)
`prompt-marked-80x24.svg` drew `prompt$ ls` as one undecorated run (`terminal-p5-r8`, no `font-weight`, no
`text-decoration`), identical to the unmarked file, though the cell tests passed.

**Root cause (from the code):** the marked fixture emitted `b"prompt$ ls\r\nout\r\n"`, so after the `\r\n` the
cursor sat on the output row; the target pattern is matched on the cursor row only (Ruled O4), so the prompt row
was never marked at snapshot time. rich's `export_svg` **can** express the decoration (`font-weight: bold` and
`text-decoration: underline;`, rich console.py:2406-2411) — nothing is dropped by the export.

**The pattern cannot separate prompt from command:** `compile_pattern().matches(row)` is whole-string anchored
(RFC 9485), a boolean over the whole cursor row. A pattern that matches `prompt$ ls` marks the whole row, so the
input `ls` would be decorated too — against brief step 1/2 ("input `ls` carries neither"). The prompt-vs-input
separation is exactly what OSC 133 gives (PROMPT vs INPUT semantic content), which L1-prompts-osc already builds.
So the marked fixture is an **OSC 133 shell** (`\x1b]133;A\aprompt$ \x1b]133;B\als\x1b]133;C\a`), no target pattern:
the prompt run is decorated, the input run is not. This is the sanctioned OSC path, not an invented design.

**Assertions added (measurable in the SVG, never by eye):** `_svg_runs`/`_css_rule`/`_decorated` read the export's
`<text class=…>` runs and `<style>` rules. In the marked baseline the run starting `prompt$` carries both
`font-weight: bold` and `text-decoration: underline`; the run starting `ls` carries neither; in the unmarked
baseline the prompt run carries neither.

**RED first-failing line:** `tests/test_redesign_prompts.py:380` — the snapshot equality fails because the new
export carries `font-weight: bold; text-decoration: underline` on the prompt run, which the old baseline lacks
(`.lane-logs/red-marked-baseline.log`). After the fixture fix, green.

**Baselines regenerated:** only the two **marked** baselines (`prompt-marked-80x24.svg`, `prompt-marked-160x45.svg`)
changed — the fixture that produces them changed (OSC shell). The **unmarked** baselines are unchanged: their
fixture (`prompt$ ls\r\nout\r\n`, no pattern, no OSC) is untouched, and its prompt run stays undecorated. Verified
in the regenerated files: marked prompt rule `fill: #e6e6e6;font-weight: bold;text-decoration: underline;` with the
`ls` run plain; unmarked prompt rule plain.

**Gate (this round):** prompts (incl. snapshots) + engine + drive shell_marks/prompt_pattern/prompt_matcher = **208
passed** (gate1.log EXIT=0, `test_drive_prompt_pattern.py` 27 as baseline); all redesign suites isolated green
(contrast 17, focus 41, keys 20, layout 60, lists 60, lists_filter 30, review 20, review4 7, new_session 42,
rail_tint 60, round1/2/3 14+17+21 — each EXIT=0). `ruff` clean (EXIT=0), `make contract-check` green (v0.18.0,
EXIT=0), `make lint` green (engine-check verified against PIN, EXIT=0). `git diff --name-only` lists only allowed
paths. No commit, no push.

## Round 4 — a pattern-matched row decorated its typed input too (orchestrator, 2026-11-24 08:5x)
The run grouping decorated every cell of a pattern-matched row (`prompt_row or c.semantic == ...`). A pattern
matches while the cursor waits on the prompt, so at that moment the row holds only the prompt; the command typed
afterwards lands on the same row and was drawn bold + underlined too. Ruled O4 decorates **prompt cells**, and
L1-prompts-osc already keeps input/output undecorated — the pattern path had to agree.

**Ruled design:** the prompt cells of a pattern-matched row are the cells the row held when the pattern matched
(the column count of its trimmed text at that moment), and only columns below that extent are decorated, then and as
the row scrolls up; cells written later on that row (the echoed input) are not.

**Implementation:** `_update_pattern_rows` records the matched **column extent** with each tracked handle
(`_pattern_extents`, keyed by the handle value); `_pattern_rows` is now `row -> extent`. `_column_extent` counts the
trimmed text's columns (a wide glyph counts both its columns; the head spans two, its spacer tail adds none).
`_build_strip` walks the row tracking the running column and decorates a cell only when its start column is below the
extent (or it is OSC-PROMPT-marked), so the echoed input past the extent stays plain. A backend
`tracked_viewport_row(handle)` recovers one handle's viewport row so the canvas pairs handle → row → extent; the
extent lives in the canvas, so no file outside the allowed list was needed (step 3 satisfied — no STOP).

**RED first-failing line:** `tests/test_redesign_prompts.py:322` — `the echoed input got the pattern decoration`
(the new test matches the pattern on `prompt$ `, feeds the echoed `ls` + newline + output, and asserts the `ls` cells
are not decorated; on the current grouping they were). Log `.lane-logs/red-extent.log`. After the extent fix, green.

**Baselines unchanged:** the marked snapshot fixture is an OSC 133 shell (no pattern), and the unmarked fixture has
no pattern, so the exports do not depend on the pattern extent; the snapshot assertions still pass and no baseline was
regenerated this round.

**Gate (this round):** prompts (incl. snapshots + the new extent test) **23 passed**; engine + drive
shell_marks/prompt_pattern/prompt_matcher **186 passed** (drive_prompt_pattern 27 as baseline, gate1.log EXIT=0); all
redesign suites isolated green (contrast 17, focus 41, keys 20, layout 60, lists 60, lists_filter 30, review 20,
review4 7, new_session 42, rail_tint 60, round1/2/3 14+17+21 — each EXIT=0). `ruff` clean (EXIT=0), `make
contract-check` green (v0.18.0, EXIT=0), `make lint` green (engine-check verified against PIN, EXIT=0).
`git diff --name-only` lists only allowed paths. No commit, no push.
