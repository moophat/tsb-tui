# Design — the attached TUI takes its login from a file (tsb-tui, U7)

**AI-Model:** claude-opus-5-5 | **Date:** 2026-11-28 | **Kind:** design only (no code, no test run, no commit)
**Brief:** `LANE-BRIEF.md`, with input `.lane-inputs/rig-design.md` § 0 (tsb-core's `rig-tui-screen-lock`, option (b))
**Ruling it implements:** the orchestrator, 2026-11-28 (the operator silent on the stated default): the attached TUI
gains a login from a file or descriptor, as drive has.
**Corpus and contract read at:** `program/` = tsb-program `v0.26.1` (`contract.pin`), equal to `contract/`.

**Result in one line.** Items 1, 2 and 4 (except how a lock resumes) are designed below as DEFAULTs for the orchestrator
to confirm or veto by line. **Item 3, how the attached TUI's lock screen resumes under a file login, is BLOCKED.** The
corpus lets only *a client with no screen of its own* re-read the login password from its file, and nothing in it says
what a client that has a screen does. The answer changes what the screen lock means for a person at that screen.
§ 3 names the clauses and sets out three options. The lock-related parts of items 4, 5 and 6 are written for each
option.

---

## 0. What exists today (read, not run)

- **The interactive TUI** (`tsb_tui/__main__.py:45-61`) takes `--broker --ca-file --target --smoke --profile --config
  --set`. It has no `--user` and no password source. The Login screen is a username `Input` and a `password=True`
  `Input` (`tsb_tui/screens/login.py:19-25`). It logs in through `app.call_api(client.login, username, password)`
  (`login.py:55`), applies `locks` from the login answer and then from `/auth/me` (`login.py:67,80`), and switches to
  the workspace.
- **The lock screen** (`tsb_tui/screens/lock.py`) has one `password=True` field. Enter clears the field and sends
  `client.screen_unlock(password)` (`lock.py:60-74`). It unlocks only for the generation that sent the password
  (`lock.py:76-80`). A 401 goes to Login (`lock.py:83-86`), and any other refusal re-enables the field and shows the
  core's message. `ScreenLock` (`tsb_tui/screen_lock.py`) holds the idle clock, `lock`, `unlock`, `lock_now`, and the
  `hold`/`after_unlock` queues. Under drive it delegates to `DriveScreenLock` and never pushes `LockScreen`
  (`screen_lock.py:100-119`).
- **Drive's password file** (`tsb_tui/drive/runner.py:672-675,697-729`):
  - `--user` and `--password-file` go together; otherwise `parser.error` (exit 2).
  - `-` means the first line of stdin, after which stdin carries commands.
  - Otherwise the file is read by `read_password_file` (`tsb_tui/drive/commands.py:139-181`). It accepts a regular file
    or a pipe (`/dev/fd/N`) and refuses a directory, a device or anything else before opening it. It warns on stderr
    when a regular file is group- or other-readable (Windows: a notice instead). It reads at most 1025 bytes and takes
    the first line without its line end. Its errors are "does not name a file", "is not a regular file or a pipe",
    "could not be read: …", "holds a password longer than 1024 bytes", "is not UTF-8 text" and "is empty". None of
    them carries what was read.
  - A read error is `parser.error("--password-file: <why>")`, exit 2. An empty stdin line is "the password is empty".
  - The login fills the Login screen's fields and presses Enter (`runner.py:321-367`). A refusal exits 1 with
    `login failed: <why>` (`runner.py:757-763`).
- **Drive's headless re-check** (`tsb_tui/drive/screen_lock.py`):
  - `reset(path)` keeps a path for re-reading only when it names a regular file and is not a descriptor (`/dev/stdin`,
    `/dev/fd/*`, `/proc/*/fd/*`) (`screen_lock.py:29-48`).
  - `resume()` runs on the sender's next refused command. It re-reads that file with `regular_only=True` on a thread and
    sends `screen_unlock` (`screen_lock.py:79-133`).
  - Outcomes: 204 unlocks; 401 means the login ended; 429, 5xx and a transport failure retry at the next command; a 403
    or an unreadable file is final until a new login.
  - A stdin or pipe login cannot be re-checked: "log in again with a regular password file".
- **Tests that cover these:**
  - `tests/test_screen_lock.py` and `tests/test_screen_lock_races.py`: the attached lock, against `tsbc serve`
    (`VaultServe`), on a fake clock.
  - `tests/test_drive_screen_lock.py`: drive's lock, the re-read, stdin and pipe logins, the retries, and "the password
    never appears in any answer, log, event or capture".
  - `tests/test_drive_commands.py:922-963,1138-1148`: `--password-file -`, the file login and the Windows mode notice.
- **Textual 8.2.8, the pinned version (`pyproject.toml`).** I read its source; I ran nothing.
  - `App._fatal_error` renders the crash traceback with `show_locals=True` (`textual/app.py`, `_fatal_error`), and rich
    prints a string local of up to 80 characters in full.
  - The Linux driver reads its keys from `sys.__stdin__` (`textual/drivers/linux_driver.py`).
  - Both facts bind item 1.

## 1. The flags

**D1 — `tsb-tui [...] --user USER --password-file PATH`.** The two go together, as in drive.
- **Help text:** "log in as this user at start, with the password from PATH: a regular file or a descriptor such as
  /dev/fd/N; never an argument and never the environment".
- **Why:** session model §7 (`program/srs/session-model-and-lifecycle.md`) says a client agent driving the TUI
  "attached as a person attaches" "hands that password only to its surface, by a file or a descriptor". Only drive
  offers that door today.
- **Errors:**
  - One flag without the other: `parser.error("--user and --password-file go together")`, exit 2. This is drive's text
    and drive's exit code.
  - With `--smoke`, either flag is refused: `parser.error("--user and --password-file are for the interactive TUI;
    --smoke takes its password from TSB_SUPERADMIN_PASSWORD_FILE")`, exit 2. The smoke path has its own reader
    (`tsb_tui/smoke.py:36-60`). Ignoring the flags there would let a caller believe they took effect (Rule 12, fail
    loud).

**D2 — the descriptor form is a path, `/dev/fd/N`, and there is no `--password-fd`.**
- **Why:** this is drive's descriptor door (`drive/GUIDE.md:25`), with the same reader. A second flag would be a second
  code path with no new capability.
- **Windows:** a file only, with drive's mode notice.

**D3 — `--password-file -` is refused for the attached TUI**, exit 2:
`--password-file: - is stdin, which the attached TUI reads its keys from; give a file or a descriptor such as /dev/fd/N`.
Nothing is read from stdin before the refusal. Why:
- Textual reads keys from `sys.__stdin__`. If stdin is a pipe that carried the password, the TUI has no keyboard.
- If stdin is the terminal, reading a line before Textual takes it would be a cooked-mode read with echo on, so the
  password would show on the screen as it is typed. The brief forbids that ("never … a screen"). It is also typing,
  which is not "by a file or a descriptor" (§7).
- `/dev/fd/0` is not special-cased. When it is the terminal, it is a character device, and `read_password_file` already
  refuses it ("is not a regular file or a pipe").

**D4 — the file checks are drive's, unchanged.**
- **The reader:** `drive.commands.read_password_file(path)`, the same function: regular file or pipe, the mode warning,
  1024 bytes, first line, UTF-8, not empty.
- **The errors:** read at start, **before Textual takes the terminal**, and reported as `parser.error("--password-file:
  <why>")`, exit 2, with drive's words. So the message reaches a normal terminal, not the alternate screen.
- **Import lint:** importing the reader from `tsb_tui.drive.commands` is inside `tsb_tui` and passes the import lint.
- **Where the attached TUI differs from drive, and why:**
  - (a) `-` is refused (D3).
  - (b) A refused login does not exit 1; it stays on the Login screen (D7).
  - (c) After the login, the path is kept for a re-read only under item 3's options A and B (§ 3).

**D5 — where the password lives, and where it never goes.**
- **Never in argv:** argv carries only the path.
- **Never in the environment:** no knob or variable takes it. `config._env_overlay` already refuses an unknown
  `TSB_TUI_*` name (`tsb_tui/config.py:506-517`), and no `password` knob is added.
- **Never in a log:** nothing logs the value. Every error is `read_password_file`'s words or the core's `message`.
- **Never on a screen:** the value never enters an `Input`, so no masked bullets show its length (§ 2).
- **Never in a crash dump.** Textual dumps locals, so:
  - main() reads the file into a one-shot holder: a small class whose `__repr__` is `<password: withheld>` and whose
    `take()` returns the value once and clears it. The holder is handed to the app. No frame holds the value in a
    named local.
  - The login runs in one worker function, `client.login(user, holder.take())`, with the value passed inline. That
    function catches anything other than `ApiError`, `HttpError` and `OSError` and re-raises it as
    `FileLoginCrashed(type(exc).__name__) from None`.
  - `from None` stops rich from walking into the contract client's frames, where `password` is a parameter.
  - `ApiError`, `HttpError` and `OSError` already end in `on_error` (`app.py:826-833`) and never crash.
- **Corpus:** credential §10.4 (`program/srs/credential-protection-model.md`) says a surface holds nothing beyond what
  the web holds, so the TUI writes no copy anywhere. §7: "never prints, logs".
- **Out of scope, flagged only:** the *typed* paths already hold the password in named locals: `login.py:49`,
  `lock.py:63`, and the `args` tuple of `_run_blocking`. A crash at the wrong moment would render a typed password in
  Textual's dump. Not fixed here (surgical); worth its own fix.

## 2. The login

**D6 — the Login screen comes up in file mode and sends the login with no key pressed.**
- **Shown:** the username field filled with `--user` and disabled. The password field empty and disabled. The status
  line "Logging in as USER with the password from the file…". The button disabled.
- **The login:** `on_mount` sends it through `call_api` with D5's worker function.
- **Success:** the existing `_on_login_done` and `_on_me_done` run unchanged (`login.py:58-86`), so `locks` are applied
  from the login answer and then `/auth/me`, exactly as for a typed login (screen-lock step 1).
- **Why the screen is not skipped:** the same path as a typed login keeps one login implementation. Drive logs in through
  that screen for the same reason (`runner.py:321-322`, "as a person does"). An agent reading the pane sees where it
  is.
- **Why the field is not filled:** filling the password `Input` would put the value in a widget, whose bullets show its
  length. A refused login would also leave it in the widget (`_on_login_error` does not clear the field).

**D7 — a refused login stays on the Login screen as the ordinary form.**
- **On 401, 429 or a transport failure:** the username stays filled; the password field is enabled, empty and focused;
  the button is enabled.
- **The error line:** "The login with the password from the file was refused: <the core's message>" on 401 and 429. A
  transport failure shows its own text, as `_on_login_error` does today.
- **No retry:** the file is not read again and no second login is sent. A person may type from there.
- **Why:** an agent reading the pane sees the refusal. Retrying would spend the user's failed-login budget (login step 2;
  screen-lock "shares the login's guess budget"). Falling back to the form costs nothing.
- **Alternative (veto by line):** exit 1 with `tsb-tui: login failed: <why>` on stderr, as drive does. That is cleaner
  for a script but abrupt for a person.

**D8 — an unreadable file, or content that is not a password, never reaches the screen.** Every such case is caught at
start (D4), exit 2.

**D9 — "Typable" is not a TUI check.**
- The TUI never types the value: it travels in the JSON body of `POST /auth/login`. `LoginRequest.password` is a string
  of 1 to 1024 characters (`contract/openapi/tsb.openapi.json`), and the core judges it.
- Drive has no such check either. `typable` is the rig's own check, because the rig types.
- A file holding control characters logs in if the core accepts them. Only the typed fallbacks (D7, and the lock
  screen's field) could not reproduce it. A person cannot type such a password anywhere today.

## 3. The screen lock for a file login — BLOCKED

### What the corpus says

- **Frontend §10.4** (`program/srs/frontend-extensibility-model.md`):
  - "The login password unlocks it … checked by the broker."
  - The file re-check is granted to one kind of client only: "**A client with no screen of its own**, such as the TUI
    driven headless … types nothing until the broker re-checks the login password, which it reads again from the file
    or descriptor its login came from, never from a command."
- **credential §5.5:**
  - "The user unlocks it with the login password … A client with no screen of its own locks the same way and resumes
    with the login password too."
  - "The desktop client and the TUI unlock and lock as the web does."
- **`program/contract/protocols/screen-lock.md`:**
  - Step 2: "MUST resume only on a 204 from the re-check."
  - Only *a headless client* "MUST read the password again from the file or descriptor its login came from".
  - Security considerations: "A headless re-check proves the source, not the sender."
- **Session model §7:** a client agent drives the TUI "attached as a person attaches" and "hands that password only to
  its surface, by a file or a descriptor". That covers the lock's password as much as the login's.
- **Frontend §10.2:** the screen lock is a guardrail in the direct topology. It is not a control, and the copy must say
  no more.

### Why the corpus does not already settle it

§7 puts a client agent in front of the *attached* TUI and lets it hand over its password only by file or descriptor.
§10.4 and the contract give a file re-check only to a client with **no** screen. For a client with a screen they say
"the user unlocks it with the login password", and they never say a file may stand in for the person at that screen.

These are not two frozen clauses in conflict: §10.4 does not forbid a file re-check for a client with a screen. It is a
gap. But every way of filling it decides what the lock means for a person at a TUI started with `--password-file`:
- either that person can lift the lock without the password,
- or the agent cannot lift it without typing.

That is a reading of §10.4, credential §5.5 and screen-lock step 2, which is the operator's (the brief: "a corpus
change is the operator's, never this job's"; AGENTS.md rule 7 carve-out). Options A and B also need words added to
`program/contract/protocols/screen-lock.md`, which is a tsb-program PR, never a TUI PR. **I have not chosen one.**

### The options

Common to A and B:
- **The re-readable path:** the path is kept for a re-read only if it names a regular file and not a descriptor. This is
  `DriveScreenLock.reset`'s rule (`drive/screen_lock.py:37-48`), moved into one shared function that both callers use,
  so drive's behaviour does not change.
- **Which lock:** only the login that came from the file has a file re-check. The TUI records the `_login_gen` of the
  file login; a lock under any later login (D11) has none.
- **The re-read:** each re-check reads the file again with `read_password_file(path, regular_only=True)` in a worker,
  passes the value inline to `client.screen_unlock`, and wraps unexpected exceptions as in D5. No copy is kept, because
  "keeps no copy" is the contract's phrase for the headless client.
- **Outcomes, mirroring drive:**

  | Outcome | What happens |
  |---|---|
  | 204 | `ScreenLock.unlock(screen)` runs, behind the existing generation guard (`lock.py:76-80`) |
  | 401 | the existing `notify_api_error`, back to Login (`lock.py:83-86`) |
  | 403 | the lock and the login stay (screen-lock step 4, "MUST NOT log out on it"). `#lock-error` shows "The password in the file was refused." The file door is withdrawn for this login, so the user's failed-login budget is not burned again |
  | Unreadable file | the same, with "The password file could not be read." |
  | 429, 5xx or transport | the core's message, or the transport text, is shown, and the door stays for the next trigger |

- **The typed field stays**, unchanged (`lock.py:60-74`). A person who knows the password resumes as today.

**Option A — an explicit key on the lock screen re-reads the file.**
- **On screen:** under a re-readable file login the lock screen shows one more note line, "Ctrl+R: re-check with the
  password file this login came from" (no path shown), and binds `ctrl+r` to the re-check.
- **A person at the screen *can*:** lift the lock without knowing the password, with one key.
- **A person at the screen *cannot*:** see session content before a 204.
- **What it means:** the lock then guards nothing against a walk-up. It proves the source, not the person, as the
  headless clause says of drive, but here there is a person.
- **Corpus:** it extends §10.4's file re-check to a client with a screen, and it reads credential §5.5's "the user
  unlocks it with the login password" as satisfied by a key press. It needs the operator's corpus edit and a
  screen-lock.md amendment.
- **The rig:** it presses `ctrl+r` through `tmux send-keys`. The password is never typed.

**Option B — a trigger the keyboard cannot reach; the keys still need the password.** The re-check from the file runs
only on an out-of-band trigger from someone who already has the agent's powers:
- **B1, a signal:** `SIGUSR1`, POSIX only, through `loop.add_signal_handler`. Only the same uid or root can send it, and
  they can read an owner-only file anyway.
- **B2, the password's own arrival:** the login's source is a FIFO, and each line written to it while the screen is
  locked is the re-check. This departs from drive, which never re-reads a pipe, and needs a reader thread.

What a person can and cannot do, and what it costs:
- **A person at the screen *can*:** type the password, as today.
- **A person at the screen *cannot*:** lift the lock with any key. In raw mode Textual turns off `ISIG`, so no key
  becomes a signal.
- **What it means:** keys at the screen keep today's meaning, and the file door has the same strength as drive's: whoever
  can read the file or signal the process passes.
- **Corpus:** it is still a file re-check on a client with a screen, so it needs the same corpus edit as A, though a
  narrower one: "an out-of-band trigger, never a key".
- **Cost:** a control channel that a person attached to the TUI does not have (§7: "as a person does"). B1 has no
  Windows door.

**Option C — no file re-check; the lock screen takes only the typed password.**
- **What changes:** the file gives the login only. The lock screen is exactly today's.
- **Corpus:** no change to the lock.
- **The cost to §7:** an agent attached to the TUI cannot resume a lock without typing its password, so it cannot meet
  §7's file-or-descriptor terms.
- **The rig:** it still needs the rig design's option (a) ruling to type at the lock, or exit 6 re-scoped (rig design
  options (a) and (c)). The login half of the rig no longer types.

**Rejected, and why:**
- **Drive's own trigger, any key or the next input:** with a screen, every key is anyone's, so the lock would lift for
  whoever touched the keyboard. It is option A without the explicit step.
- **Silent auto-unlock when the time passes:** that is no lock at all. It breaks screen-lock step 2, "resume only on a
  204", in spirit.

**My recommendation, not taken:**
- **B1:** it keeps what a key at a locked screen can do exactly as today, and the file door matches drive's proof. Its
  cost is a POSIX-only door and one corpus sentence.
- **A** is the simplest to build but empties the lock for a person.
- **C** needs no corpus change but leaves §7's attached agent without a door at the lock.

## 4. Lock now, sign out and re-login under a file login

**D10 — Lock now** (Menu ▸ Lock now and its key; `screen_lock.py:157-165`) is unchanged: `POST /auth/lock` purges the
user's vault unlocks and this client's screen locks (credential §5.5; Frontend §10.4). It resumes exactly as an idle
lock does under the option chosen in § 3:
- A: `ctrl+r` or the typed field;
- B: the trigger or the typed field;
- C: the typed field only.

Neither Lock now nor its resume touches the vault unlock: the passphrase never resumes a screen lock, and this password
never unlocks the vault.

**D11 — sign-out and the end of a login do not log in from the file again.**
- **When:** after Menu ▸ Sign out (`app.py:878-922`), or a 401 that ends the login (expiry, revocation, a core restart,
  an account change; login steps 9, 12, 13, 18).
- **What shows:** `_to_login` pushes the ordinary Login form. The username is filled from `--user`, the password field
  is empty, and no file is read or sent.
- **To log in from the file again:** start the TUI again.
- **Why:**
  - An automatic re-login would undo a sign-out at once.
  - A "log in with the file" button would let a person at a signed-out screen log in as the agent, which is option A's
    problem at the login door.
  - A pipe could not be read twice anyway.
- **The lock after a typed re-login:** that login did not come from the file, so its lock takes only the typed password
  under every option. Frontend §10.4 says "the file … its login came from".
- **Alternative (veto by line):** an explicit "Log in with the password file" button on the form, re-reading a regular
  file only. It has the walk-up cost and would need the same operator reading as option A.

## 5. Tests, each committed red first

All tests run against `tsbc serve` (`VaultServe`, `RecordingVaultServe`, the input-recording fixture) on the fake
clock of `tests/test_screen_lock.py`, never a real broker. They go in a new `tests/test_password_file_login.py`, plus
one parametrization added to `tests/test_main_broker.py` for exit codes. Every test also checks that the password is
absent from captured stdout and stderr, from `caplog`, and from every screen dump it takes. **Each test is committed
failing before the change that greens it.** "Fails when" names the regression it catches (Rule 9).

### Flags and start (items 1, 2)

| # | Test | Asserts | Fails when |
|---|---|---|---|
| 1 | `test_user_and_password_file_go_together` | either flag alone exits 2 with "--user and --password-file go together" | the pair check is dropped or reworded |
| 2 | `test_password_file_dash_is_refused_and_stdin_is_not_read` | `-` exits 2 naming stdin as the TUI's keys; a line given on stdin is still unread afterwards | `-` reads stdin as drive does |
| 3 | `test_password_file_errors_are_drives` (parametrized: missing, directory, character device, empty, 1025 bytes, not UTF-8) | exit 2, and stderr is `--password-file: ` followed by exactly `read_password_file`'s words for the same input; no app is built; no content on stderr | the attached TUI uses its own reader or other words, or builds the app before checking |
| 4 | `test_a_group_readable_file_warns_as_drive_does` | the same WARNING line as drive, and the login goes on | the mode check is skipped |
| 5 | `test_password_file_with_smoke_is_refused` | exit 2 with D1's message | `--smoke` silently ignores the flags |
| 6 | `test_no_flag_or_variable_takes_a_password` | the parser has no option taking a password value; `TSB_TUI_PASSWORD` is a `ConfigError` | a password flag or knob is added |

### The login (item 2)

| # | Test | Asserts | Fails when |
|---|---|---|---|
| 7 | `test_a_file_login_reaches_the_workspace_with_no_key` | the server sees one `POST /auth/login` with `--user` and the file's first line; the pilot pressed no key; the workspace is up; `screen_lock.settings` equals the served `locks` | the login needs a key, or skips `_on_login_done` and loses `locks` |
| 8 | `test_the_file_password_never_enters_a_widget_or_the_screen` | during and after the login, `#password.value == ""`; no dump holds the value or a run of bullets | D6 is replaced by filling the field |
| 9 | `test_a_refused_file_login_shows_the_ordinary_form_and_does_not_retry` | on 401: the error line holds the core's message; the username is filled; the password field is empty, enabled and focused; exactly one `POST /auth/login` | it retries, exits, or leaves the field filled |
| 10 | `test_a_crash_during_the_file_login_dumps_no_password` | with `client.login` raising `RuntimeError`, the rendered `_exit_renderables` (Textual's locals dump) do not contain the value | the value is a named local, the holder's `repr` shows it, or `from None` is dropped |

### Sign out and re-login (item 4, independent of § 3)

| # | Test | Asserts | Fails when |
|---|---|---|---|
| 11 | `test_sign_out_after_a_file_login_reads_no_file` | after Sign out, the Login form shows `--user` with an empty password; the file, deleted after start, causes no error; no `POST /auth/login` is sent | sign-out logs in again from the file |
| 12 | `test_a_401_after_a_file_login_returns_to_the_ordinary_form` | the same after a served 401 | a 401 triggers a file login |
| 13 | `test_lock_now_under_a_file_login_locks_and_calls_the_operation` | `POST /auth/lock` is sent; `LockScreen` is up; no session byte is on screen | Lock now behaves differently under a file login |

### The lock under option A or B

Add these if the operator rules A or B. `<trigger>` is `ctrl+r` for A, `SIGUSR1` sent to the app's handler for B1, and
a line on the FIFO for B2.

| # | Test | Asserts | Fails when |
|---|---|---|---|
| 14 | `test_the_trigger_re_checks_from_the_file_and_resumes` | `POST /auth/screen-unlock` carries the file's content; on 204 the lock lifts and output that came meanwhile is on the grid | the trigger is missing or does not send |
| 15 | `test_the_file_is_read_again_at_each_re_check` | the file is rewritten between the login and the lock, and the re-check sends the new content | the TUI keeps a copy from the login |
| 16 | `test_a_refused_file_re_check_keeps_the_lock_and_the_login_and_withdraws_the_door` | after a 403: still locked, no trip to Login, the message is shown; a second trigger sends nothing | 403 logs out, or the door stays and burns the limiter |
| 17 | `test_an_unreadable_file_keeps_the_lock_and_sends_nothing` | file removed, or replaced by a FIFO (A, B1): no request, the message is shown | the re-read follows a non-regular file |
| 18 | `test_429_and_transport_failures_keep_the_door_for_the_next_trigger` | the message is shown; the next trigger sends again | a transient failure is treated as final |
| 19 | `test_a_descriptor_login_has_no_file_re_check` (A, B1 only) | a `/dev/fd/N` login: the trigger sends nothing; the typed field still works | a pipe is re-read |
| 20 | `test_the_typed_field_still_resumes_a_file_login_lock` | the typed right password gives a 204 and unlocks | the typed path is removed |
| 21 | `test_a_late_file_re_check_never_unlocks_a_newer_login` | a 204 that arrives after Sign out and a new login lifts nothing | the generation guard is skipped |
| 22 | `test_a_typed_login_after_sign_out_has_no_file_re_check` | after D11's typed re-login, the trigger sends nothing | the file door outlives its login |
| 23 | `test_the_file_re_check_dumps_no_password_on_a_crash` | as test 10, for `screen_unlock` | the re-check holds the value in a named local |
| 24 (B only) | `test_no_key_lifts_a_file_login_lock` | every key except a typed right password with Enter (including `ctrl+r`, Enter on an empty field, and the leader) sends no re-check with the file's content and leaves the lock up | a key reaches the file door |
| 25 (A only) | `test_the_lock_screen_names_the_file_door_and_no_path` | the note line is present; the path is absent from the dump | the copy leaks the path or omits the door |

### The lock under option C

| # | Test | Asserts | Fails when |
|---|---|---|---|
| 14C | `test_a_file_login_lock_takes_only_the_typed_password` | no key and no signal sends a re-check; the copy is unchanged; the file is never opened after the login | a file door appears |

**Unchanged and run green alongside:** `tests/test_screen_lock.py`, `tests/test_screen_lock_races.py`,
`tests/test_drive_screen_lock.py` (above all `test_a_stdin_or_pipe_login_stays_locked_until_a_new_login`, which pins
the shared re-readable rule), and `tests/test_drive_commands.py`. The gate is `make contract-copy-check lint test`.

## 6. What tsb-core's rig then needs

After a tsb-tui release with this change and a `deploy/tui.pin` bump, the rig starts the pane with `tsb-tui --profile
harness --broker <TSB_URL> --target <TSB_TARGET_ID> --set size.auto_request=true [--ca-file …] --user <owner>
--password-file <the path in TSB_SUPERADMIN_PASSWORD_FILE>`.
- **The file must be regular and owner-only.** A `/dev/fd/N` cannot reach a tmux pane, because the pane is spawned by
  the tmux server, not by the rig.
- **Login:** the rig waits for the workspace (`▦ Targets`). A Login form with the refusal line is a FAIL. No password is
  typed or pasted.
- **The checks that stand as written:** the rig design's L0–L5 and L7–L8, with the probe and the wrong value
  `PROBE + WRONG` typed into the lock field (not the password).
- **L6, the unlock, depends on the ruling in § 3:**
  - A: `send-keys C-r`.
  - B1: `kill -USR1` to the pid from `tmux display-message -p -t tsbsl '#{pane_pid}'`. The pane runs `env … tsb-tui`,
    and `env` execs, so that pid is the TUI's (not verified).
  - B2: a line written to the FIFO the rig created.
  - C: the rig design's `paste_secret`, under its option (a) ruling.
- **What it removes:** under A or B the rig needs no `load-buffer` and no typed secret at all. `paste_secret`, its probe
  SKIP and its test 17 go away, and the rig design's D1 BLOCKER is resolved by this change rather than by a reading of
  §7.

## 7. Decisions to confirm or veto by line

- **D1** (§ 1): the flags `--user` and `--password-file`; refused with `--smoke`.
- **D2** (§ 1): the descriptor form is `/dev/fd/N`, with no `--password-fd`.
- **D3** (§ 1): `-` is refused.
- **D4** (§ 1): drive's reader and words, checked before Textual starts, exit 2.
- **D5** (§ 1): the one-shot holder with a withheld `repr`; inline passing; `from None` on unexpected errors.
- **D6** (§ 2): the Login screen in file mode, logging in with no key and never filling a widget.
- **D7** (§ 2): a refused login stays on the ordinary form and does not retry. The alternative is exit 1, as drive.
- **D8** (§ 2): an unreadable file or bad content is an exit 2 at start, never on screen.
- **D9** (§ 2): no typability check.
- **D10** (§ 4): Lock now is unchanged and resumes as § 3 decides.
- **D11** (§ 4): no file login after sign-out or a 401. The alternative is an explicit button.
- **BLOCKER — § 3:** option A, B (B1 or B2) or C. This is the operator's reading of Frontend §10.4, credential §5.5
  and screen-lock step 2 against session model §7. A or B also needs a screen-lock.md amendment in tsb-program.

## What I did not verify

- I ran nothing: no test, no TUI, no tmux. The Textual facts (the locals in the crash dump, keys read from stdin) come
  from reading the pinned 8.2.8 source, not from a run.
- That raw mode keeps `ISIG` off under Textual 8.2.8 on every POSIX terminal. Option B's "no key is a signal" rests on
  it.
- That `env` execs, so `#{pane_pid}` is the TUI's pid (§ 6, B1).
- That rich does not walk into `__context__` once `__suppress_context__` is set, for every exception type the
  contract client can raise. I read rich's traceback walker; I did not exercise it.
- I did not run the AGENTS.md session-start steps (the rotate script, WORK_TRACKER, a SESSION file): the brief allows
  one file, and the ledgers are the orchestrator's.
- I did not read `tsb_tui/drive/GUIDE.md` whole, only its password-file and screen-lock sections (lines 9-33, 73,
  134, 330-343, 403-437).

Status: BLOCKED
Summary: Items 1, 2 and 4 are designed as vetoable DEFAULTs: `--user` with `--password-file PATH|/dev/fd/N`, `-`
refused, drive's checks, a Login screen that logs in with no key, and no file re-login after sign-out. How the attached
lock screen resumes under a file login (item 3) is a corpus gap, since Frontend §10.4 and screen-lock.md give the file
re-check only to a client with no screen while session model §7 puts an agent at the attached TUI. Options A (a key),
B (an out-of-band trigger; B1 recommended) and C (typed only) are set out for the operator, with the tests and the
rig's unlock step for each.
