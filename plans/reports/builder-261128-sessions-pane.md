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
is then the last row of the list and is reached by scrolling, as the rows are. The rule is unchanged. Known limit,
accepted by the coordinator: with four or more open sessions at 80x24 the line is the last row and is reached by
scrolling.

## Not verified

- No run against staging.
- The line's click opens the full view through the existing full-view command; no test clicks it.
