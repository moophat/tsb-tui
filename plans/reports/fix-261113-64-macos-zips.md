# macOS zip review round

AI-Model: gpt-6.1-sol

Status: DONE_WITH_CONCERNS

## Ruling and changes

5. The orchestrator ruled that PR CI deliberately fetches no packaging inputs.
The macOS test module now follows the Windows built-zip precedent: it is
explicitly deselected in the same Packaging tests command. The comment says
that the release job builds the zips from pinned inputs and checks them before
publishing. The no-skip rule is unchanged. The workflow regression test failed
before this change and passes after it.

1. Added `Test the macOS zips` between assembly and publishing. Both targets
are required, with no missing-archive skip. The step mounts the staged archives
read-only and runs `test_staged_macos_zips`, reusing `check_zip` for required
layout, executable launchers/interpreters, LF launchers, bytecode exclusion,
authored-text leaks, engine marker and checksum. Scratch files go to pytest's
writable directory. The workflow regression test failed because the step was
absent. Both interpreter binaries and the engine are checked through
`check-macho.py`. Its optional executable mode checks Mach-O magic, executable
type, CPU architecture and subtype; existing strict dylib checks are unchanged.
Two interpreter architecture tests failed before that mode existed and now
prove that correct architecture passes and wrong architecture fails.

2. Both POSIX launchers prefer a same-name file in the current directory when
the invocation has no slash, then fall back to PATH. An unresolved name emits
one run instruction and exits nonzero. Stub-interpreter tests reproduce bare
shell invocation, with and without an executable decoy on PATH, verify bundle
paths and argument preservation, and check both launchers' missing-name messages.
The shell and missing-name tests failed before the fix. Existing direct,
symlink and space-containing directory checks still pass.

3. The macOS README now uses `./tsb-tui --broker https://<host>`, consistent
with `./tsb-tui-check`. Windows keeps its existing command. Real assembly tests
for both targets failed on the old README and now assert both commands.

4. Added the container HOME override to the macOS build. The workflow test
checks every project-code container invocation in the release job; its red
failure identified the macOS build step.

## Red evidence

Focused launcher/README/workflow run, before fixes:

```text
12 failed, 34 deselected in 38.91s
pytest exit code: 1
```

These were four shell/decoy launcher cases, two bare-name launcher cases,
one missing-name message case, two README cases, and three workflow cases
(release validation, container HOME, ruled CI deselection).

Interpreter architecture mode, before its fix:

```text
2 failed, 19 deselected in 0.36s
pytest exit code: 1
```

An intermediate staged-check harness run also failed: 2 failed, 17 deselected,
exit 1. That failure was a scratch-directory argument mismatch during test
construction, not evidence of a product defect; it was corrected.

## Verification

Focused final launcher/interpreter checks: 16 passed, 6 deselected, exit 0.
The first combined run passed 226 tests with 5 skips and 17 passing subtests,
exit 0; a final combined run was required because the interpreter tests and
second missing-name case were added while the first run was active.

`make lint` was run once: exit 0. Its test phase reported 102 passed; ruff,
import lint, repository guards and vendored engine verification passed.
`git diff --check`: exit 0.

Local skips: two Windows launcher build/PE checks need the CI image's pinned
mingw-w64 compiler; three installer tests need its pinned PowerShell/Pester.
The macOS inputs were already cached, so both real macOS assemblies ran.

Not verified: native macOS execution, the Docker release job, or GitHub Actions
on the remote runner. No commits, pushes or git configuration changes were
made. Only authorized implementation/test paths and this report were changed.

Final required combined command:

```sh
python3 -m pytest -q -p no:cacheprovider packaging/ .github/scripts/tests/ tests/test_installers.py
```

```text
229 passed, 5 skipped, 17 subtests passed in 189.77s (0:03:09)
pytest exit code: 0
make lint exit code: 0
```

The initial red command selected `bare_name or missing_name or shell or
readme_commands or release_checks_macos or release_containers or pr_macos`
from the macOS packaging tests and installer tests. The additional red command
selected `macho_interpreter_architecture` from the macOS packaging tests.
These counts and failures are retained above; temporary raw logs were removed.

Final status within the allowed paths (status command exit 0):

```text
 M .github/workflows/release.yml
 M .github/workflows/test.yml
 M packaging/assemble.py
 M packaging/engine/check-macho.py
 M packaging/launchers/tsb-tui
 M packaging/launchers/tsb-tui-check
 M packaging/tests/test_macos_build.py
 M tests/test_installers.py
?? plans/reports/fix-261113-64-macos-zips.md
```
