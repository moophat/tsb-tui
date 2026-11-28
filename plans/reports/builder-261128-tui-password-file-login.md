# Builder report — the attached TUI logs in from a password file (the login half)

**AI-Model:** claude-sonnet-5-5 | **Date:** 2026-11-28 | **Design:** `plans/reports/design-261128-tui-password-file.md`
**Scope built:** § 1 (D1-D5), § 2 (D6-D9), § 4 D10-D11 as far as they need no § 3 option; § 5 tests 1 to 13.
**Not built (not ruled):** § 3, how the lock screen resumes under a file login. The lock screen is exactly today's under
every login, and no file is read after the login. Tests 14 to 25 and 14C are not written. Not committed.

## What I built

- `tsb_tui/file_login.py` (new): `FileLogin`, the one-shot holder (`user`, `pending`, `take()` once and clears, `repr` is
  `<password: withheld>`); `login_from_file(client, holder)`, the login worker, which calls
  `client.login(holder.user, holder.take())` with the value inline and re-raises anything other than `ApiError`,
  `HttpError` and `OSError` as `FileLoginCrashed(<type name>) from None` (D5).
- `tsb_tui/__main__.py`: `--user` and `--password-file PATH` (D1). One without the other: drive's words, exit 2. With
  `--smoke`: D1's message, exit 2. `--password-file -`: D3's message, exit 2, with stdin untouched. The file is read with
  drive's `read_password_file` and reported as `--password-file: <drive's words>`, exit 2 (D4, D8), before the app is built
  and before Textual takes the terminal. The value goes straight into the holder. The app gets it as `file_login=`.
  `--password-file` has no `-`/fd-number flag of its own: `/dev/fd/N` is a path (D2). No knob or variable takes a password.
- `tsb_tui/app.py`: the `file_login` constructor argument, kept as `app.file_login`.
- `tsb_tui/screens/login.py` (D6, D7, D11):
  - The first Login screen, while the holder is pending, shows the username filled and disabled, the password empty and
    disabled, the button disabled and "Logging in as USER with the password from the file…", and sends the login on
    mount through `call_api`. Success runs the unchanged `_on_login_done` and `_on_me_done` (so `locks` apply).
  - A refusal enables the form again with the username kept, the password empty and focused, and shows
    "The login with the password from the file was refused: <core's message>" on 401 and 429. No retry.
  - A later Login screen (sign-out, a 401) is the ordinary form with `--user` filled and the password field focused; no
    file is read and no button is added.
- `README.md` (Quick start) and `tsb_tui/drive/GUIDE.md` (the `--user`/`--password-file` row): one paragraph and one
  sentence describing the attached TUI's pair.

## Small choices the design does not spell out (flag if you want them otherwise)

1. **Where the file is read:** after the config and broker are resolved, before the app is built. A bad config still exits
   1 and a missing broker 2 before the file is looked at; the pair, `--smoke` and `-` checks run right after parsing.
2. **The status line** is the existing `#login-error` Static: the "Logging in…" text, replaced by the error on refusal.
3. **The "was refused" prefix** is on 401 and 429 only, as § 2 D7 says. Any other `ApiError` shows the core's plain message,
   as a typed login does. A failure of `/auth/me` after a good login re-enables the form with its plain message.
4. **A second `take()`** raises `RuntimeError` (no value in its text); nothing calls it twice.
5. `tests/test_main_broker.py` is not changed: the design's "one parametrization" was not needed, the exit codes are in the
   new file.
6. I kept `_parse_args` in `__main__.py` (`tests/test_smoke.py` imports it); the new checks call `build_parser()` for
   `parser.error`.

## Tests, red then green (`tests/test_password_file_login.py`, 19 test items = design tests 1 to 13)

Python and `ruff` from the test venv, `NO_COLOR` unset.

**Red** (before any change to `__main__.py`, `app.py`, `login.py`; only the holder class existed so the test module could
import): `python -m pytest tests/test_password_file_login.py -p no:cacheprovider -q` → **19 failed, exit 1**. Reasons:
tests 1 to 5 `unrecognized arguments: --user ...` (the parser has no such flag), test 6 the set of password options is
empty, tests 7 to 13 `TypeError: TsbTuiApp.__init__() got an unexpected keyword argument 'file_login'`. The 19 items are
test 1 (2 cases), test 3 (6 cases) and one item each for tests 2, 4, 5, 6 and 7 to 13.

**Green** (after the change): same command → **19 passed, exit 0**. The first green runs also found two test-side
errors, fixed in the test: the fixture's `make_session` logs in with the fixture password (test 13 serves that
password), and Textual clears `_exit_renderables` after printing, so test 10 reads the crash report while the app holds it.

**Mutation checks** (each made the named test fail, then was reverted; no mutation is left in the tree):

| Mutation | Failed |
|---|---|
| `from None` dropped in `login_from_file` | test 10 (password in the crash dump) |
| password bound to a local in `login_from_file` | test 10 |
| holder `repr` shows the value | tests 8, 10 and the group-readable test |
| file mode on every Login screen (not only the first) | tests 11 and 12 |
| no focus on the password field after a refusal | test 9 |
| refusal text without the prefix | test 9 |
| `-` allowed | test 2 |
| `--smoke` ignores the flags | test 5 |

## Gates

- `ruff check tsb_tui tests bench tools`: exit 0. `python tools/import_lint.py`: clean, exit 0.
  `.github/scripts/docs_branch_guard.py`: exit 0 (no-op off a PR). `.github/scripts/leak-check.sh`: exit 0 (it skips
  itself here). `tools/check-engine.sh`: exit 0. `make contract-copy-check`: exit 0.
- `make lint` as one command exits 2, **on a clean tree too** (checked with my changes stashed): two tests in
  `.github/scripts/tests` and `tools/test_import_lint.py` run `pip wheel` of the contract copy, which fails in this
  environment; 121 pass. The lint steps after that test run were therefore run one by one, as listed above.
- The brief's fallback set (new file, `test_main_broker`, `test_screen_lock`, `test_screen_lock_races`,
  `test_drive_screen_lock`, `test_drive_commands`, plus `test_login_completion`, `test_windows_guards`):
  **152 passed, exit 0**.
- `make test` ran 3000 s and was killed by my timeout at about 66%, with one failure in `test_redesign_focus.py`. I
  then ran the whole suite once with `python -m pytest tests/ -p no:cacheprovider -q` (67 minutes on this machine):
  2814 passed, 3 skipped, **4 failed**. Two were mine: `tests/test_smoke.py` imports `_parse_args`, which I had removed;
  fixed (see 6 above) and the file passes. The other two, `test_remember.py::test_remember_saves_after_a_201_for_phase_a_fields`
  (`NoMatches '#login-password'`) and `test_update_check.py::test_slow_request_does_not_block_login_and_worker_is_bounded`
  (the update worker still running), are timing-sensitive and **pass when run on their own** after my change
  (139 passed, exit 0 for those two files with `test_smoke`, the new file and `test_main_broker`). I did not run them on a
  clean tree, and I did not re-run the whole suite after the `_parse_args` fix.

## Files changed (`git diff --name-only`, plus the two new files)

    README.md
    tsb_tui/__main__.py
    tsb_tui/app.py
    tsb_tui/drive/GUIDE.md
    tsb_tui/screens/login.py
    tsb_tui/file_login.py            (new)
    tests/test_password_file_login.py (new)
    plans/reports/builder-261128-tui-password-file-login.md (this report, new)

No file under `program/`, `contract/`, `tsb_tui/drive/` code, `screen_lock.py`, `screens/lock.py`, plans or ledgers is touched.

## What I did not verify

- No run in a real terminal, no tmux, no `/dev/fd/N` through a real process substitution (the reader is drive's and its
  descriptor path is covered by drive's tests; mine reads regular files).
- A 429 on the file login is not served by a test (the 401 path covers the prefix and the form); the prefix on 429 is code
  only.
- The Windows branch (drive's mode notice) is not run.
- That the crash dump holds no password for exception types other than `RuntimeError` raised by `client.login`, or for a
  crash elsewhere in the login (for example in `_on_login_done`).
- Test 7's "the pilot pressed no key" holds because the test presses none; nothing asserts that no key was needed beyond it.
- The AGENTS.md session-start steps (rotate script, ledgers) were not run: the brief forbids ledgers.
- The two timing-sensitive tests above on a clean tree; the full suite after the last edit.

Status: DONE_WITH_CONCERNS
Summary: The login half (D1-D11 as confirmed, tests 1-13) is built and green, with no lock-screen change and no commit. Concerns: `make lint` fails on `pip wheel` in two tests even on a clean tree, `make test` exceeds a 50-minute run here, and two timing-sensitive tests failed once under load but pass alone.
