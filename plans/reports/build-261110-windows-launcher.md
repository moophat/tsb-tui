# Build report: the Windows launcher tsb-tui.exe

Branch `feat/windows-launcher-exe` on 63b3ef4. Not committed, not pushed.

## Pins

    $ cd program && git describe --tags --exact-match && git rev-parse HEAD
    v0.14.1
    ad31180f02b48760321070110e2c42a0aafff75a
    $ cat program/srs/.source   (vault_commit)
    51c977da5cc3aa23bda3cce444f384cf2087c00c   (exported 2026-11-01T22:33:37+07:00, vault_state clean, 24 files)

## Step 0: who calls `tsb-tui.cmd` by name

- This repo, outside PHASE.md and plans/: `README.md:27`, `docs/using-tsb-tui.md:82,395`, `packaging/assemble.py`,
  `packaging/tests/test_packaging.py` (all edited here), nothing else. No script, workflow or tool calls it.
- `tsb-core's harness` and `tsb-core's deploy` (read-only): no match.
- `plans/` of this repo: no match. `tsb-tui.cmd` stays in the zip beside the exe, as ruled.

## Steps and evidence

1. Image. `gcc-mingw-w64-x86-64` is a metapackage that also installs the posix-thread variant; to get the win32
   thread model the Dockerfile pins the variant package and the two packages that decide the bytes:
   `gcc-mingw-w64-x86-64-win32=12.2.0-14+deb12u1+25.2+b1`, `binutils-mingw-w64-x86-64=2.40-2+10.4`,
   `mingw-w64-x86-64-dev=10.0.0-3` (versions from `apt-cache policy` on the image's own base). Built locally as
   `tsb-tui-ci-p7` (id bc77c68d9293, 634 MB; the unmodified `ci-tui-py` is 275 MB). In it:
   `x86_64-w64-mingw32-gcc (GCC) 12-win32`, `Thread model: win32`.
2. `cmdline.c`/`cmdline.h` (no Win32 call) with its host test, then `tsb-tui.c` (267 lines). Compiles clean with
   `-Wall -Wextra`.
3. `packaging/build-launcher.sh` (one gcc command, flags of step 9, output `packaging/cache/tsb-tui.exe`). Two builds:
   both `476595b7b0b4129d933c026a86cf0712132da886136817df6e7d5a4daf52b0dc`, 21504 bytes. A third build from a copy
   mounted at a different path gave the same hash.
4. Inspect (`x86_64-w64-mingw32-objdump -p`): `pei-x86-64`, PE32+ (0x20b), Characteristics 0x22e (no DLL bit),
   Subsystem 3 (Windows CUI). Imports: `KERNEL32.dll`, `msvcrt.dll`. No libgcc, winpthread.
5. `assemble.py` copies `tsb-tui.exe` (stops with a message naming `build-launcher.sh` if it is missing), the two
   `.cmd` files; README.txt names `tsb-tui`. Full zip built in the image from a scratch commit tagged v0.0.1 with a
   dummy engine: `tsb-tui-0.0.1-windows-x64.zip`, 46461593 bytes, sha256
   `c661b7e94a21afff2f75f065c1ebbf70db5c5f8c576555af4b123bebcdb0e894`; a second build gave the same sha256. It holds
   `tsb-tui.exe` (21504), `tsb-tui.cmd`, `tsb-tui-check.cmd`, no `config.toml`. `packaging/tests` in the image
   against that zip: 27 passed, 16 skipped (host-compiler tests; the image has no host gcc).
6. `release.yml`: step "Build the launcher" before "Build the Windows zip", same container.
7. Docs: `README.md`, `docs/using-tsb-tui.md` (two lines), `packaging/README.md`.

## Tests, and "fails before"

- Host (`gcc`): 16 command-line cases pass (plain name, quoted path with spaces, tabs, `&`, `^`, `%PATH%`,
  trailing quote, unquoted path with spaces, unterminated quote, empty).
- In the image: determinism and PE-shape tests pass (2 passed).
- `test_packaging.py`: new `test_missing_launcher_fails_before_the_zip`, `test_assembled_bundle_holds_the_exe_and_never_a_config`,
  `test_launcher_sources_carry_no_leaks`; `test_built_zip` extended (exe present, MZ, no `config.toml`, README).
- Fails before: the new test files copied into a detached worktree of 63b3ef4: host run gave 2 failed (the two new
  assembly tests: no exe in the bundle), 16 errors (no `cmdline.c`); in the image the two build tests failed with exit 127
  (no `build-launcher.sh`).

## Gates

- `make lint PYTHON=<the test venv's python>`: exit 0.
- `make test PYTHON=<the test venv's python>`: exit 2 twice, NOT green. Run 1: 1 failed / 1900 passed (31 min), a 30 s
  timeout in `tests/test_snapshots.py` ("the coloured screen"). Run 2: 1 failed / 1900 passed, a different test in the same
  file (`test_s1_the_rail_widens_in_place_into_the_fleet_sidebar[120x40]`, "nothing is drawn beside the sidebar").
  Each failing test passes alone (whole `test_snapshots.py`: 89 passed; the s1 test: 3 passed), and this change touches
  nothing under `tsb_tui/`; the box was shared with another pytest run. Treat as load-dependent flakes, not proven
  unrelated: a quiet-box run is needed to get exit 0.
- `make test` runs `tests/` only; `packaging/tests` is run by hand (this report: 40 passed host, 27+16 skipped in image).

## Decisions I took inside the spec (flag if wrong)

- Job-object set-up failures other than `AssignProcessToJobObject` (`CreateJobObjectW`, `SetInformationJobObject`) also
  warn and carry on, same as step 7's fail-open. PHASE.md names only the Assign failure.
- Pause (7-E) uses `_getch` (msvcrt), after the message.
- ENGINE is decoded as UTF-8, read whole (capped at 1 MiB), first line only.

## Design gaps (STOP items, not built)

- `config.toml.example`: PHASE.md's Files table says the zip ships it, but no such file exists and the table assigns
  its content to phase 8 without naming who creates it or where it lives. Not shipped here. Options: (a) phase 8 adds
  the file and the assemble line together; (b) this phase adds a one-line placeholder at `packaging/launchers/`.
- The CI image lacks a host C compiler, so the command-line split test skips there (and `packaging/tests` is not in
  `make test`). Options: add `gcc libc6-dev` to the image, or keep it a by-hand test.

## Not verified (the Windows PC checklist)

Everything that runs on Windows: the exe starting at all, AppLocker/WDAC/Defender behaviour, verbatim arguments
reaching Python, the four environment variables inside Python, the engine loading, exit codes (including
`0xC000013A`) returned, Ctrl-C and Ctrl-Break handling, the job object ending Python on window close, the
double-click pause, conhost versus Windows Terminal, long paths and non-ASCII folders. No wine (ruled out).

Status: DONE_WITH_CONCERNS (make test not green on this box)

## Qodo round 1

1. `CreateProcessW` failure and a failed resume of the suspended child now go through the same path as the missing
   python and ENGINE errors (`launcher_error`, which pauses only when the launcher is the sole process on its console);
   a failed resume still terminates the child first. The pause decision is a one-line comparison of
   `GetConsoleProcessList` with 1 and needs a Win32 console, so it has no host test; the PC checklist covers it. The
   job-object warnings stay non-pausing.
2. `.github/ci/py.Dockerfile` pins `gcc=4:12.2.0-3`, `gcc-12=12.2.0-14+deb12u1`, `libc6-dev=2.36-9+deb12u14` (from
   `apt-cache policy` on the image's base); `.github/workflows/test.yml` has a "Packaging tests" step in the same image
   that fails on any skip. Local image, then removed: `packaging/tests` gave 42 passed, 0 skipped, 1 deselected.
   Decision taken: the step deselects `test_packaging.py::test_built_zip`, which needs a finished release zip that only
   the release job builds; the alternative is building a zip (network inputs, tag) in the test job. Say if you want that.
   Risk: a point-release pin such as `libc6-dev` can vanish from the mirror when bookworm updates it, as for the mingw pins.

## Qodo round 2

**AI-Model:** gpt-6.1-sol

- Added a release step immediately after the Windows zip build, before asset staging or publishing. The build moves
  the zip and checksum to `$RUNNER_TEMP/winzip`; the test container mounts it read-only at `/work/packaging/out`,
  where `test_built_zip` looks. A failing test fails the release step.
- Exact command, with the same CI image and user/workspace mounts as the build:

  ```sh
  docker run --rm \
    --user "$(id -u):$(id -g)" -v /etc/passwd:/etc/passwd:ro -v /etc/group:/etc/group:ro -e HOME=/tmp/home \
    -v "$GITHUB_WORKSPACE:/work" -w /work \
    -v "$RUNNER_TEMP/winzip:/work/packaging/out:ro" \
    ci-tui-py python -m pytest packaging/tests/test_packaging.py::test_built_zip -p no:cacheprovider
  ```

- Expected inputs: release-built `tsb-tui-<version>-windows-x64.zip` and matching `.zip.sha256` in
  `$RUNNER_TEMP/winzip`, plus the checked-out packaging test. No environment variable selects the zip.
- Local zip build/test not run: the installed `ci-tui-py` lacks `x86_64-w64-mingw32-gcc`. Rebuilding from
  `.github/ci/py.Dockerfile` needs apt network inputs beyond `packaging/inputs.lock`, including unpinned packages.
  With that image available, a clean version-tagged checkout builds with `packaging/build-launcher.sh`, then
  `packaging/build-zips.sh windows-x64 --engine packaging/engine/windows-x64`; the packaging downloads are pinned.
  Move the resulting zip and checksum to `$RUNNER_TEMP/winzip` before running the command above.
- `make lint PYTHON=<the test venv's python>`: passed (exit 0).
- No commit or push. Status: DONE_WITH_CONCERNS (local built-zip execution not verified).
