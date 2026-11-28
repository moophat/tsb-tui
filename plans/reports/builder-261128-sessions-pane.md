# Sessions list: own sessions that are not open here

Branch `fix/sessions-pane-detached`. Model: claude-sonnet-5-5.

## Finding

The side panel's Sessions list shows only the sessions open in this window (README, "The Sessions list shows the
sessions open here; the full view lists the rest"). After a detach nothing is open here, so the list showed 0 while
the login's two detached sessions were alive. The model, the filter, the 287 ended records and their order do not
matter: `sidebar_view` (framed branch) lists `GROUP_OPEN_HERE` and, behind `e`, the ended ones.

## Ruling applied

The "open here only" rule stands. The list adds one line when the login owns live or detached sessions that are not
open here: `N more · F full view` (`F` is the side panel's key for the full view). Other logins' sessions are not
counted. In the empty state the line follows the empty message.

## Change

- `tsb_tui/widgets/sidebar.py`: `SidebarModel.mine_elsewhere`; `_mine_elsewhere_line`; the framed view appends the
  line after the rows as a one-line row of the list; `sessions_height` adds that line and keeps its rule (at least
  3, at most half the panel); `_empty_lines` appends it to the empty copy.
- `tsb_tui/screens/terminal.py`: `_sidebar_model` counts the login's live or detached records not open here.
- `tests/test_sessions_list_detached.py`: 287 killed records first, then three detached (two the login's, one the
  peer's), at 80x24 and 160x45.

## Red and green

- Red (source changes stashed, test file only): `4 failed, 2 passed`, exit 1. The four are the line tests (empty
  list, and one open here with one more) at both sizes; the line is absent, for example the painted list reads
  `▸1 sw-one ● live · yours you type` with no `1 more · F full view`. The two that pass are the full-view guard
  (the full view already lists both detached sessions of the login and not the peer's), a guard and not a red.
- Green, new file plus the list, layout, sidebar and painted-row test files: `186 passed`, exit 0.
- Load, new file, 10 loops in each of 3 parallel copies: 0 failed loops.
- `make lint` (venv-setuptools path first): exit 0.
- Full suite: `1 failed, 2804 passed, 3 skipped`. The one failure is
  `tests/test_vault_unlock.py::test_an_org_credential_whose_administrator_holds_no_unlock_asks_no_passphrase`
  (a painted-text wait in the vault dialog, code this change does not touch). It fails under load and passes alone
  (`1 passed`, exit 0); treated as a flake, not part of this change. An earlier run stopped at 61% with this test
  green.
- Four baselines changed because those scenarios give the login a detached session not open here, so the list now
  carries the line (the same four tests pass on the code before this change): in `tests/snapshots/redesign-p3/`,
  `group-row-80x24.svg`, `group-row-160x45.svg`, `recent-three-80x24.svg`, `recent-three-160x45.svg`. Regenerated
  with `UPDATE_P3_SNAPSHOTS=1`; the only difference is the added `N more · F full view` line (and the section's
  extra row).

## Height rule

Nothing open here, both sizes: the empty copy (2 lines at 80 columns) plus the line is 3 lines, inside 5 rows
(at 80x24 the cap is 12 rows, at 160x45 it is 22). One open row plus the line fits at both sizes. With four or more
open sessions at the narrow budget (three lines each) the rows already exceed the cap (12 rows at 80x24); the line
is then pinned as the section's last visible row and the session rows scroll in the space above it (Round 2). The
height cap is unchanged.

## Not verified

- No run against staging.
- The line's click opens the full view through the existing full-view command; no test clicks it.

## Round 2 — the line stays visible

CodeRabbit on #138: the `N more · F full view` line sat after the session rows in the scrolled list, so with four
open sessions at 80x24 it could not be reached. The ruling: pin it as the section's last visible row.

**Change** (`tsb_tui/widgets/sidebar.py`): in the framed view the line stays the last of `lines` but has no entry in
`row_lens`, so it is no row and the selection model (`view.rows`, `_move`, End, the wheel) is untouched. The widget
reserves the section's last content row for it (`_pinned_line`, `_session_room`): the scroll walk, the painted end
and the maximum scroll use the inner height less that row; `_line_at` and `line_offset` answer the line for that row
whatever the scroll. `sessions_height` and its cap are not touched. The empty state is unchanged.

**Red on f88d61a** (the two new cases, last and first session selected, before the fix):

    FAILED tests/test_sessions_list_detached.py::test_the_line_stays_painted_whatever_row_is_selected[True]
    FAILED tests/test_sessions_list_detached.py::test_the_line_stays_painted_whatever_row_is_selected[False]
    E  AssertionError:  ▸ Sessions · 4/4 ━━━━━━ ... (rows sw-open-2..4, no `1 more · F full view`)   [True]
    E  AssertionError:  ▸ Sessions · 1/4 ━━━━━━ ... (rows 1..3, no line)                               [False]
    2 failed, 6 deselected     exit 1

**After**, `python -m pytest tests/test_sessions_list_detached.py -q`: `9 passed`, exit 0. The file holds the
earlier 7 tests, the 2 new cases above (the line is the last content row; the sidebar is 23 rows tall; the height
equals the cap; End stops at the last session and the line stays painted) and one click test: a click on the pinned
line opens the full view. The click test was not run red on f88d61a.

**Commands and exit codes** (the test venv's `bin` first on PATH):
- `make lint`: exit 2. `ruff check`: all checks passed; the tool tests pass. Two failures, both in
  `.github/scripts/tests/test_contract_tarball.py` (`pip wheel --no-build-isolation` exits 2: the known missing
  setuptools). Reported, not fixed. 121 passed, 2 failed.
- `make test`: exit 2 before any test ran, because the clone's git metadata is not usable here (see Not verified).
  I ran what it runs, `env -u NO_COLOR python -m pytest tests/ -p no:cacheprovider`, in pieces (the suite takes about
  an hour on this machine): `418 passed` (exit 0), `381 passed` (exit 0) and `625 passed` (exit 0): 1424 tests, tests 1388–2811 of the 2811 collected. The first 1387
  tests (through `tests/test_quiet_prompt.py`) ran in a full-suite run that I had to kill at about 51% on the tool's
  30-minute limit: every test it printed passed, but it has no exit code. `tests/test_redesign_lists_filter.py`,
  `tests/test_sidebar_painted_rows.py` and the rest of the sidebar-rendering files are in the pieces that exited 0
  or in that first part.
- Regenerated baselines: none. No snapshot changed, including the four `redesign-p3` ones from round 1.

**Not verified**
- Not committed. The worktree's git directory belongs to another lane's clone, and this user cannot write its index (`index.lock: Permission denied`); git also refuses the directory as
  owned by another user. I did not work around the permission. `make test` and `make contract-copy-check` fail on
  that, not on the code. The change is in the working tree for the orchestrator to commit as
  `fix(tui): keep the sessions list's "more" line visible` with trailer `AI-Model: claude-sonnet-5-5`.
- Tiny sidebars: with the section at its 3-row minimum (sidebar height 7 or less) the pinned line takes the one
  content row and no session row paints, where before a one-line row would. The ruling says "whenever", the cap is
  unchanged, so I followed it literally; it is not tested, and it may deserve a ruling.
- Wheel and Down behaviour is tested through End only; the wheel calls the same `_move`.
- No run against staging.

**Files changed** (`git diff --name-only f88d61a`, the working tree, since nothing is committed; `f88d61a..HEAD`
is empty):

    plans/reports/builder-261128-sessions-pane.md
    tests/test_sessions_list_detached.py
    tsb_tui/widgets/sidebar.py

### Round 2b: the tiny sidebar

Ruling: pin the line only when a session row also fits above it in the section's content rows; with one content
row, a session shows and the line does not. F opens the full view in every case.

**Change** (`tsb_tui/widgets/sidebar.py`): `_pinned_line` is true only when the view has the line, has rows, and the
shortest row fits in the inner height less one. When it is false the line is in the view but outside the painted
range, so it is not painted and the rows keep the whole section, as before round 2.

**Test** (`tests/test_sessions_list_detached.py::test_the_line_is_pinned_only_when_a_session_row_fits_above_it`):
160 columns (a session is one line), the login's one session open here and others elsewhere, the window shrunk
after login to 8 rows (one content row) and 9 rows (two content rows).

**Red on the round-2 working tree** (one content row), before the change:

    1 failed, 1 passed, 9 deselected     exit 1
    E  AssertionError:  ▸ Sessions · 1/1 ━━━━━━━━━━━━━━━━━━━━
    E    4 more · F full view
    E  assert False   (no `sw-open-1` row painted: the line took the only content row)

The two-content-row case passed on that tree, as it should (both paint).

**Green after:** `tests/test_sessions_list_detached.py`: `11 passed`, exit 0.

**Exit codes** (the test venv's `bin` first on PATH):
- the 48 test files that mention the sidebar or the snapshots, in three runs: `400 passed` exit 0, `517 passed`
  exit 0, `326 passed` exit 0 (1243 tests; they include `tests/test_redesign_lists_filter.py`,
  `tests/test_sidebar_painted_rows.py` and the detached-sessions file). No baseline regenerated.
- `make lint`: exit 2; `ruff` passes; the only failures are the two known `test_contract_tarball.py` ones
  (missing setuptools), 121 passed.

**Not verified**
- Not committed (the git directory is not writable). The other test files were not re-run this round, only the
  sidebar and snapshot ones.
- "A session row fits" is read as: any row of the list fits whole above the line (the shortest). With rows of
  different lengths (ended rows shown with `e`) the selected row can be a longer one that does not fit while a
  shorter one does; the line is then pinned and the selected row scrolls as before. Not tested.
- At the narrow width a session row is three lines, so the line is pinned only from four content rows
  (a sidebar of 12 rows and up); below that the section shows the row and no line.
- No run against staging.

### Round 3: the selected row fits

Ruling: pin the line only when the selected session row fits wholly above it; otherwise the rows get the whole
section and the line is not painted. F still opens the full view.

**Change** (`tsb_tui/widgets/sidebar.py`): `_pinned_line` now tests the selected row's line count (the first row
when none is selected, the position `_scroll_to_selected` already uses) against the inner height less one, instead
of the shortest row's.

**Test** (`tests/test_sessions_list_detached.py::test_the_selected_row_must_fit_above_the_line_to_pin_it`):
CodeRabbit's case. 80 columns, a sidebar of 10 rows, one session open here (three lines) and one visible ended
session (two lines), `row_lens == (3, 2)`, three content lines, the open session selected, and the login's other
sessions elsewhere. The test asserts those preconditions, then that the open row paints and the line does not.

**Red on d04bf09** (the round-2b code):

    1 failed, 11 deselected     exit 1
    E  AssertionError:  ▸ Sessions · 1/2 ━━━━━━
    E    (two blank content rows)
    E    3 more · F full view
    E  assert False   (no `sw-open-1` row painted: the shortest row fit, the selected one did not)

**Green after:** `tests/test_sessions_list_detached.py`: `12 passed`, exit 0.

**Exit codes** (the test venv's `bin` first on PATH):
- the 48 test files that mention the sidebar or the snapshots, in three runs: `400 passed` exit 0, `517 passed`
  exit 0, `327 passed` exit 0 (1244 tests, including `tests/test_redesign_lists_filter.py` and
  `tests/test_sidebar_painted_rows.py`). No baseline regenerated.
- `make lint`: exit 2; `ruff` passes; the only failures are the two known `test_contract_tarball.py` ones
  (missing setuptools), 121 passed.

**Not verified**
- Not committed. The test files outside the sidebar and snapshot set were not re-run this round.
- The pin follows the selection: moving the selection between a row that fits and one that does not shows or hides
  the line (the window repaints at each move). Not tested beyond the case above.
- With no selected row the first row decides, as `_scroll_to_selected` does; not tested.
- No run against staging.
