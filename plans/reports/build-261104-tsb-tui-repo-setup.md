# tsb-tui repository setup — 2026-11-04

## Result

Status: BLOCKED on one Ruff finding outside the authorized safe-only fixes. All requested done
checks were rerun with the existing venv tools on PATH:

| Check | Current result |
| --- | --- |
| `make contract-check` | PASS, exit 0: clean v0.12.1 pin, `53caef07e91e`. |
| `make lint` | FAIL, exit 2: FURB162 only, `tsb_tui/model/countdown.py:22`. |
| `env -u NO_COLOR PYTHON=$TEST_VENV/bin/python make test` | PASS, exit 0: **1,577 passed**, zero skipped/failed/deselected, 1,504.26s (25m04s). |
| Guard/import-lint tests | PASS: **49 passed**, zero skips, 3.34s. |

Ruff's exact `check --fix` command applied 117 safe fixes. No unsafe fix, disabled rule or new
configuration was used. One FURB162 remains because Ruff classifies its fix as unsafe; the
application-edit ruling permits only safe-fix output, so no manual removal was made. A request
for the exact one-line scope exception was presented; none has been granted.

The Makefile test recipe now unsets `NO_COLOR`. All 30 earlier visual failures pass without any
assertion/rendering fix. The reference deploy-key trust model remains unchanged, as explicitly
ruled at 20:14. No commit, push, branch/remote/config change, install or network fetch was made
in this clone. Test processes exited. The all-green done criterion remains unmet solely by lint.

Earlier verification tables and the initial failure inventory below are historical; the final
20:14 verification update records the current outcome.

## Inputs and rulings

Read the brief, the TUI split step-0 ruling (2026-11-04 19:12), phase 0's rebuild list and step 5,
and the supplied core/web gate references. Source-checkout locations below are named symbolically rather
than publishing workstation paths that the unchanged environment-leak pattern intentionally rejects.
`CORE` means the read-only tsb-core checkout; `REF_CORE` and `REF_WEB` mean `.review/from-core/` and
`.review/from-web/`; `TEST_VENV` means the pre-existing test venv named in the brief.

Applied the operator's follow-up rulings:

- Copy the engine headers at the same PIN and list all of them alongside the shared object in `SHA256SUMS`.
- `bench/_common.py` and `bench/engine_read.py` are in scope for path fixes only.
- Retain the reference import lint's explicit `rich` allowance as Textual's rendering library.
- A base-branch `.pr_agent.toml` confirmed absent means no branch is exempted from review. Git errors and
  malformed TOML still fail the guard.
- Guard tests may create commits and push/fetch between disposable local repositories under pytest's
  temporary directory. They do not change this clone's history, branch or remotes and need no network.

## Added gates and inputs

- `Makefile`: Bash with `-eu -o pipefail`, `PYTHON ?= python3`, `contract-init`, `contract-check`,
  `test` depending on the pin check, and `lint`. Missing directories/scripts fail. Ruff is optional only
  outside CI, as in the reference. Lint runs Ruff over `tsb_tui tests bench tools`, the guard tests and
  import-lint tests, import lint, docs guard, environment-leak check and engine integrity check.
  Python bytecode writes are disabled so importing the pinned contract does not dirty `program/`.
- `vendor/libghostty-vt/`: shared object, unmodified PIN, all 35 headers under `include/`, and all
  source licence files under `licenses/`. `SHA256SUMS` contains 36 entries: the shared object and every
  header. The object's SHA-256 is
  `9393a23d5bd3634875386c6f71a380277d6ced76a4040a8b32fcec0097ffefaa`.
- `tools/check-engine.sh`: strict checksum verification; rejects malformed/duplicate checksum entries,
  missing header entries, missing headers, and a shared-object hash differing from PIN's `so_sha256`.
- `tools/import_lint.py`: retains the reference AST checks, alias/dynamic import checks, network module
  restrictions, test-only allowances, reserved TSB names, parse failures and symlink refusal. Its one
  extracted unit comprises `tsb_tui/`, `tests/` and `bench/`, keeping `program/` and infrastructure tools
  out of the consumer boundary. Missing unit directories fail. Runtime allowances remain `textual`,
  `rich`, `argon2`, `precis_i18n`, `tsb_contract_client` and `tsbc`.
- `tools/test_import_lint.py`: temporary allowed and forbidden fixtures, all six requested core-unit
  names across application/tests/bench, dynamic aliases, relative escapes, parse errors, `.pyi`,
  symlinks, local name laundering, test-only helpers and missing inputs.
- `.github/workflows/test.yml`: explicit `test` and `lint` jobs on `[self-hosted, tsb-tui-ci]`, full
  checkout, Python image, web's deploy-key-isolated program fetch, key deletion, then `make contract-init`
  and `make contract-check`. No test path filters. Test output is checked for skipped/hidden cases.
- `.github/ci/py.Dockerfile`: the core Python 3.12 slim-bookworm digest, Git/make/Bash/CA/SSH tools,
  this repo's exact runtime and pytest manifest pins, and Ruff 0.16.9. No contract installation.
- `.github/workflows/secrets.yml`: core-style action, gitleaks 8.24.3, `contents: read` and
  `pull-requests: write`, TUI runner labels.
- `.github/scripts/leak-check.sh`: core's pattern unchanged, scanning this repo's tracked files without
  recursing into the submodule. The script excludes only itself because it declares the pattern.
- `.github/scripts/docs_branch_guard.py`: `plans/`, `.aiworkflow/`, `docs/`, and Markdown paths;
  branch exemptions read from the base configuration. Absence is confirmed through a successful empty
  `git ls-tree` result; failures remain failures.
- `.github/scripts/tests/test_docs_branch_guard.py` and `test_leak_check_prefixes.py`: adapted core
  cases, pytest-provided temporary roots, TUI paths, and added absent-base/Git-error/docs-directory cases.
- `.gitleaks.toml`: one `[allowlist]` table; empty because the core's xterm/harness false-positive
  exceptions are not inputs in this repo. Default gitleaks rules remain enabled.
- `.pr_agent.toml`: core's docs-only source branch exemptions.
- `.pre-commit-config.yaml`: core's gitleaks v8.30.0 hook and environment-leak warning hook.
- `.aiworkflow/rotate-work-tracker.py`: exact reference copy. No ledger Markdown files added or changed.
- This report. The orchestrator-provided `tools/contract-pin.sh` is retained byte-for-byte, not rewritten.

## Every test and benchmark path fix

| File | Change |
| --- | --- |
| `tests/conftest.py` | `REPO_ROOT` is `TUI_DIR`, not its parent. Contract is root `program/contract`. Engine is root `vendor/libghostty-vt/libghostty-vt.so`, with `TSB_TUI_TERM_LIBRARY` honored when present. Updated old pin/input instructions accordingly. |
| `tests/fleet_support.py` | Contract root uses `parents[1]` instead of `parents[2]`. |
| `tests/model_frames.py` | Same contract-root correction. |
| `tests/prompt_support.py` | Same contract-root correction. |
| `tests/test_model_alerts.py` | Same contract-root correction. |
| `tests/test_model_attachment.py` | Same contract-root correction. |
| `tests/test_sidebar_painted_rows.py` | Same contract-root correction. |
| `tests/test_term_engine.py` | C4 fixture path uses this repo's `program/contract`. |
| `tests/vault_support.py` | C7 fixture path uses this repo's `program/contract`. |
| `tests/test_feed_thread.py` | `_lib()` uses conftest's resolved `TERM_SO`, including its environment override, instead of the containing checkout's `vt/` path. |
| `tests/test_view_and_quit.py` | Engine PIN is root `vendor/libghostty-vt/PIN`. |
| `tests/test_term_abi.py` | Root uses `parents[1]`; headers, PIN and default object are under `vendor/libghostty-vt/`. The object constant honors `TSB_TUI_TERM_LIBRARY`; loaded-library assertions still use the shared `term_so` fixture. |
| `tests/golden_sidebar_painted.py` | Root is `TESTS_DIR.parent`; engine uses `vendor/` and preserves a supplied override before clearing other TUI variables. Manual commands drop the old `tui/` prefix; archive extraction no longer strips the package directory. The generator was not run and golden data was not regenerated. |
| `bench/_common.py` | Root is the package-containing repo itself; contract and default engine use this repo's layout. No benchmark behavior changed. |
| `bench/engine_read.py` | Default object is under this repo's `vendor/libghostty-vt/`, not the containing checkout's `vt/`. No benchmark behavior changed. |

## Every README change beyond the history section

1. Replace the sibling `../tsb-program/spine/plan.md` filesystem claim with an explicitly named
   tsb-program document, and replace the obsolete `tui/` write boundary with this repository's ownership.
2. Both protocol links and the displayed protocol directory drop the obsolete `../` prefix.
3. The smoke rig and `manage.sh verify` references explicitly name their tsb-core owner.
4. The drive guide's `harness/` rig reference explicitly names tsb-core.
5. Install references change `tui/pyproject.toml` to `pyproject.toml` and `pipx install ./tui` to `pipx install .`.
6. The post-deploy hook and following smoke-rig reference explicitly name tsb-core.
7. The editable test install's working-directory comment changes from `tui/` to the repo root.
8. The engine prerequisite becomes the vendored path and documents `TSB_TUI_TERM_LIBRARY`.
9. The no-skips reference explicitly names `tsb-core/harness/README.md`.

## Commands and results

All shell commands ran from the clone root unless stated as read-only source inspection. No installation,
network fetch, Docker build, SSH, rig or non-tiny benchmark was run. Implementing helpers and logs live only
in the already ignored `.review/` directory. Verification uses the existing venv tools and sets
`PYTHONDONTWRITEBYTECODE=1`, `TMPDIR=$PWD/.review/tmp`, and pytest's `--basetemp` beneath `.review/`.

### Inspection and implementation commands

The repeated read-only inspections are grouped by command and their explicit inputs; output was read,
not copied into an external file. Missing optional files or paths returned nonzero as noted.

| Command or invocation | Result |
| --- | --- |
| `pwd`; `rg --files -g AGENTS.md -g .review/brief.md` over the lanes parent; parent `AGENTS.md` existence checks | Confirmed clone root; no applicable root/ancestor AGENTS file. `program/AGENTS.md` is outside edit scope. |
| `cat .review/brief.md`; `rg -n` for the named ruling/phase headings; `sed -n '1257,1345p'` on the ruling; `cat` phase 0 | Read the ruling, rebuild list, step 5 and surrounding plan. |
| `find .review -maxdepth 4 -type f`; `rg --files .review/from-core` | Enumerated supplied references. |
| `cat`/`sed` the reference Makefiles, workflows, images, scripts, lint and tests, configs, rotation script, engine PIN; `rg -n` their gate/version definitions | Read reference behavior and exact pins. Some combined output was truncated; relevant sections were reread in focused calls. |
| `cat pyproject.toml tests/conftest.py README.md`; `sed`/`cat` ABI, feed-thread, benchmark-smoke, golden-generator, contract-pin and packaging tests; `rg` for old layout assumptions across tests/bench/package/README | Identified the path fixes listed above, header requirement and `rich` allowance. |
| `rg --files CORE/.github/scripts/tests` filtered for docs/leak tests; `cat` both files | Read original script test cases. |
| `git status --short`; `git branch --show-current`; `git log -1 --oneline refs/remotes/origin/main`; `git ls-tree` of `.pr_agent.toml` at HEAD/base | Branch is `chore/repo-setup`; initial untracked `tools/` was the orchestrator's script. Base config is absent. |
| `cat .gitignore`; optional config `ls`/`cat` checks | `.gitignore` and the queried optional Ruff files do not exist; these inspection commands returned nonzero. |
| `cat .git/info/exclude`; `ls -l` venv Python/Ruff | Confirmed `.review/` is ignored and existing tools are available. |
| `cat` the `ck:cook` skill | Read implementation/test/review workflow; task scope and no-commit instruction take precedence over generic finalization writes. |
| `mkdir -p .review/tmp`; `python3 .review/implement.py` | Copied vendor inputs, generated hashes and applied the listed test/benchmark/import-lint changes; expected object hash asserted. |
| Shell here-doc writes of `Makefile`, `tools/check-engine.sh`, `tools/test_import_lint.py`; `chmod +x tools/check-engine.sh tools/import_lint.py` | Created gates/tests; executable modes match shebangs. |
| `python3 .review/readme-fixes.py` | Added history notice and only the listed README path/command corrections. |
| CI/script implementation helper Python and shell writes | Created the CI/scripts/config files listed above from references; no execution of CI mutation/fetch commands locally. |
| `mkdir -p plans/reports`; report here-doc and final result update | Wrote this report at the brief's requested path. |

### Initial verification commands (before the 20:14 rulings)

| Command | Result |
| --- | --- |
| `make contract-check` with venv tools on PATH | PASS: clean `program/` at `53caef07e91e`, v0.12.1. |
| `python tools/import_lint.py` | PASS: 164 application/test/benchmark Python files. |
| `bash tools/check-engine.sh` | PASS: shared object plus all 35 headers; object hash agrees with PIN. |
| `ruff check tsb_tui tests bench tools` | FAIL: initially 107 findings. One introduced executable-mode finding was corrected by chmod; the remaining findings are described below. |
| `ruff check --show-settings tsb_tui/config.py`; `ruff check --isolated --statistics tsb_tui tests bench tools`; `ruff check --verbose --statistics ...`; `ruff --version`; `ruff check --output-format json ...` | Confirmed Ruff 0.16.9 defaults and recorded individual findings in `.review/ruff-findings.json`; isolated mode still fails. No missing core configuration accounts for these failures. |
| `git -C CORE ls-files '*ruff*' '*pyproject*'`; targeted config-file discovery | No tracked core Ruff config to carry across. |
| `git diff --stat`; `git diff README.md`; `git diff --numstat`; `git diff --check`; `git status --porcelain=v1 --untracked-files=all`; log `head`/`tail` and status `wc`; process-status inspection | Reviewed changes and suite progress; whitespace check PASS; 87 changed/untracked paths, all in the authorized scope. |
| `git diff --exit-code -- tsb_tui`; same for `pyproject.toml` | PASS: both protected paths remain unchanged. |
| Python byte comparison of `tools/contract-pin.sh` and the web reference | PASS: identical. |
| Read-only `diff -r`/`cmp` of vendored headers, licences, PIN and object against their sources | PASS: exact copies. |
| `bash -n tools/check-engine.sh tools/contract-pin.sh .github/scripts/leak-check.sh` | PASS. |
| Python `tomllib.loads` for `.gitleaks.toml`, `.pr_agent.toml`, `pyproject.toml`; `yaml.safe_load` for both workflows and precommit config | PASS: parsed all files. This is syntax validation, not execution of GitHub Actions. |
| Python comparison of source and copied `PATTERN=` definitions | PASS: environment-leak pattern unchanged. |
| `make lint` | FAIL at Ruff, before the later lint commands. Final retry also exits 2: 106 findings (99 I001, 6 UP017, 1 FURB162), split as bench 2, tests 96, tools 1, application 7. |
| `PYTHON=$TEST_VENV/bin/python make test` | FAIL (make exit 2): 1,577 collected and executed; 1,547 passed, 30 failed in 1,669.93s; zero skipped/xfail/deselected cases. |
| `python -m pytest .github/scripts/tests tools/test_import_lint.py -p no:cacheprovider` with separate `.review/pytest-gates` basetemp | PASS: 49 passed in 3.44s; 11 docs guard, 2 leak-prefix and 36 import-lint cases; no skips. |
| Python scan of `git ls-files --cached --others --exclude-standard -z` paths using the unchanged leak regex, excluding only its declaration script | PASS: 266 tracked/untracked files, no matches; no staging required. |
| `python3 .review/check-engine-negative.py` | PASS: intact scratch copy accepted; corrupted object/header, PIN disagreement and omitted header checksum all refused. Temporary copies were inside the workspace and removed. |
| Separate `python tools/import_lint.py`, `python .github/scripts/docs_branch_guard.py`, `bash .github/scripts/leak-check.sh`, `bash tools/check-engine.sh` | PASS independently. The real local docs-guard invocation is a no-event no-op; its PR behavior is covered by the temporary-repository tests. |

### Initial lint scope decision (superseded by the 20:14 safe-fix ruling)

The initial Ruff result was 99 I001, 6 UP017, 1 FURB162 and 1 EXE001. The new import-lint script's
EXE001 has been corrected by setting its executable bit. Seven existing findings are in the protected
application tree:

- `tsb_tui/config.py:12`, `saved_theme.py:7`, `theme_files.py:9`: I001 import ordering.
- `tsb_tui/model/countdown.py:22`: FURB162 timezone replacement and UP017 UTC alias.
- `tsb_tui/screens/chrome_dialogs.py:238`, `screens/terminal.py:935`: UP017 UTC alias.

Further findings are in tests and benchmarks, whose authorized changes are path fixes. No automatic
style fix was applied. Options requiring the operator's ruling are (a) authorize the necessary style-only
fixes across the affected application/tests/bench files, or (b) specify the intended Ruff rule/configuration
policy for this extracted repo. No option was selected and no rule was silently ignored.

## Not verified

- Docker image build, CI runner execution, deploy-key fetch, and CI `make contract-init`: prohibited local
  network/install/config mutations, and the new `tsb-tui-ci` runner is not yet provisioned.
- Running gitleaks 8.24.3/8.30.0 and installing/running precommit hooks: no install/network allowed.
- Real terminal/phone checks, deployed core smoke/rigs, non-tiny benchmarks: excluded by the brief.
- Rotation against real ledgers: the script was copied only; ledger writes are not authorized.
- The original leak script checks tracked files only. New untracked files are not included by `git grep`
  until staged/tracked; no files were staged to disguise that limit. A separate equivalent-pattern scan also checked the new untracked files and passed.

## Reference CI trust boundary found in review

The workflow follows the supplied web model, including fetching `program/` inside `ci-tui-py`.
That image is built from the PR's Dockerfile and package before the deploy-key fetch, so it is
PR-controlled code even when the fetch step invokes only literal Git commands. A malicious image
entrypoint or replacement Git executable can read the mounted `/ssh/id`. Removing the key before
`make contract-init` prevents later checkout scripts from reading it, but does not isolate the key
from the image used during the fetch. This inherited trust assumption was identified by review; no
secret was mounted or workflow executed locally.

The operator explicitly ruled at 20:14 that this reference trust model stays. It is retained
without redesign and is no longer an open decision. CI execution itself remains unverified.

## Initial saved-theme failures (NO_COLOR cause subsequently ruled)

The full run reached two failures in unchanged `tests/test_saved_theme.py`. A separate two-test
rerun with its own workspace-contained temporary root reproduces both (2 failed in 3.62s):

- `test_the_saved_look_is_applied_at_login`, line 271: exported SVG has no matching Nord rail fill
  at x < 4, after the configuration-name/source assertions have passed.
- `test_a_higher_layer_wins_over_the_saved_choice`, line 295: exported SVG has no matching Dracula
  canvas fill at x >= 4, after the configuration precedence/source assertions have passed.

These are screenshot fill assertions, not missing contract, library or header paths. Async-generator
cleanup errors follow the assertion failures. At the initial stop the cause was unproven, and the tests/application were not changed to force
a pass. The 20:14 ruling identifies NO_COLOR as the cause; pytest now runs without it. The earlier timing hypothesis was not established; the subsequent NO_COLOR reproduction and
passing rerun resolve the environment cause. Packaged-theme lookup is package-relative.

Command: `python -m pytest tests/test_saved_theme.py::test_the_saved_look_is_applied_at_login
 tests/test_saved_theme.py::test_a_higher_layer_wins_over_the_saved_choice -vv --tb=short`, with
a separate `.review/pytest-saved-theme` basetemp. Log: `.review/saved-theme-targeted.log`.
Read-only `rg`/`sed` inspections of these tests, their fill helper and package lookup followed; an
inspection operand for a nonexistent singular scheme module returned 2. `git diff --exit-code --
 tests/test_saved_theme.py` confirms that file is unchanged.

The 20:14 ruling resolved this group through `env -u NO_COLOR`; no rendering/assertion fix was
needed or selected. All 18 saved-theme cases pass in the final run.

## Initial full-suite outcome and failure inventory

All 1,577 unit cases ran. There are no skipped tests to list. The final failures are 2 saved-theme,
26 snapshot and 2 theme-picker cases. Their assertions concern expected SVG background, surface or
accent colors, not missing relocated files. The initial cause was unproven; the subsequent ruling identifies NO_COLOR. No rendering code,
color expectations or screenshot assertions were changed to address these failures. The complete failure tracebacks
are retained in the ignored `.review/test.log`.

- `tests/test_saved_theme.py::test_the_saved_look_is_applied_at_login`
- `tests/test_saved_theme.py::test_a_higher_layer_wins_over_the_saved_choice`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-dracula]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-nord]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-solarized-dark]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-solarized-light]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-catppuccin-mocha]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[65x30-one-half-dark]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-dracula]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-nord]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-solarized-dark]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-solarized-light]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-catppuccin-mocha]`
- `tests/test_snapshots.py::test_t1c_scheme_colours_reach_the_canvas_and_leave_the_chrome[120x40-one-half-dark]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[65x30-terracotta]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[65x30-paper]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[65x30-contrast]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[65x30-nord]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[65x30-dracula]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[120x40-terracotta]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[120x40-paper]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[120x40-contrast]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[120x40-nord]`
- `tests/test_snapshots.py::test_the_gallery_pictures_each_packaged_theme_with_its_suggested_scheme[120x40-dracula]`
- `tests/test_snapshots.py::test_a_switch_at_run_time_draws_the_picture_a_start_with_that_look_draws`
- `tests/test_snapshots.py::test_the_picker_over_T1c_shows_the_panel_and_the_previewed_scheme[120x40]`
- `tests/test_snapshots.py::test_the_picker_over_T1c_shows_the_panel_and_the_previewed_scheme[200x60]`
- `tests/test_snapshots.py::test_the_picker_on_the_phone_over_T1c`
- `tests/test_theme_picker.py::test_moving_the_lists_previews_the_look_at_once`
- `tests/test_theme_picker.py::test_using_an_edited_user_theme_again_reapplies_its_css_colours`

Final read-only checks: `git diff --check` passes; `make contract-check` again confirms the
unchanged clean v0.12.1 pin. `git status --porcelain=v1 --untracked-files=all` contains exactly
87 paths, all in the brief's scope plus the expressly authorized headers and two benchmark path
fixes. The new report is included in that count. `tsb_tui/`, `pyproject.toml`, `.gitmodules` and
the orchestrator's contract script remain unchanged. Disposable test sessions exited; the
wrapper/make/pytest PIDs were checked absent. No process was deliberately left running.

Post-run commands: `rg`/Python extraction of the 30 traceback headings and final summary;
`tail` of the lint log; protected-path `git diff --exit-code`; `git status`/path audit; final
contract check; report finalization helper. Generated Ruff cache was removed from the workspace.
The temporary-repository Git operations were confined to pytest's workspace-contained temporary
roots. No project-history/branch/remote/config mutation, install or network fetch was performed.

To complete this PR, the operator must rule on extending the path-only scope for (1) the required
Ruff style fixes, including seven findings in the protected application tree, and (2) diagnosis and
minimal fixes for the 30 visual assertion failures. An alternative is to specify an intended Ruff
policy and leave visual fixes to a separate authorized follow-up; that leaves this PR blocked.
The reference CI trust boundary is documented separately above, with its available alternatives.
No decision was made on the operator's behalf.

## Implementation after the 20:14 rulings

- Command: `PATH=$TEST_VENV/bin:$PATH ruff check --fix tsb_tui tests bench tools`.
  Ruff 0.16.9 reports 118 findings encountered, 117 fixed, one remaining. No `--unsafe-fixes`,
  lint-rule disable or new configuration was used. Log: `.review/ruff-safe-fix.log`.
- The six changed application files (`config.py`, `model/countdown.py`, `saved_theme.py`,
  `screens/chrome_dialogs.py`, `screens/terminal.py`, `theme_files.py`) contain only Ruff's safe
  import ordering and `timezone.utc` to `UTC` alias substitutions. Review found no other app edits.
  The corresponding tests/benchmark/tools fixes likewise contain only Ruff's safe edits beyond
  the previously listed path changes. No test expectation was changed.
- The `Makefile` test recipe is now `env -u NO_COLOR $(PYTHON) -m pytest tests/ -p no:cacheprovider`.
  Its one-line comment explains that NO_COLOR strips SVG colors required by visual checks.
- CI continues to use the reference deploy-key trust model. Its previously documented trust
  boundary is accepted by the 20:14 ruling, not an unresolved design choice.
- `make contract-check` passes again, clean at `53caef07e91e`, v0.12.1.
- `make lint` was rerun and exits 2 on FURB162 alone, at `tsb_tui/model/countdown.py:22`.
  The exact suggested removal is `value.replace("Z", "+00:00")` to `value` inside
  `datetime.fromisoformat(...)`. Ruff classifies this automatic fix as unsafe. The safe-only
  application-edit scope does not authorize a manual change; the exact one-line manual exception was requested, but no exception has been granted.
- Full command: existing venv PATH and PYTHON, bytecode disabled, workspace-contained TMPDIR and
  pytest basetemp, `env -u NO_COLOR make test PYTHON=$TEST_VENV/bin/python`.
  Log: `.review/test-no-color.log`. PASS: 1,577 passed, zero skipped/failed/deselected, 1,504.26s; command exit 0.
- `python -m pytest .github/scripts/tests tools/test_import_lint.py` with a separate temporary
  root passes again: 49 passed in 3.34s, zero skipped. Log: `.review/gate-tests-no-color.log`.
- Additional commands: `git status --short`, `sed` of Makefile/report/countdown source,
  `git diff --stat`, `git diff --numstat`, `git diff --check`, application diff snapshot to the
  ignored `.review/ruff-safe-app.patch`, and report-update helper. Whitespace check passes.

## Final verification after the 20:14 rulings

The full NO_COLOR-unset run passes all 1,577 tests in 1,504.26s. All 18 saved-theme, 89 snapshot,
and 42 theme-picker cases pass; all 30 initial visual failures are resolved. There are no skipped
tests to list. The 49 guard/import tests pass separately in 3.34s. The full test command exits 0,
with the explicit existing-venv PYTHON override and no NO_COLOR variable.

`make contract-check` passes. `make lint` fails only on FURB162; the remaining repository checks
were also invoked independently and pass: import lint (164 files), docs guard, leak check,
engine integrity and `git diff --check`. The unsafe automatic fix was never invoked, and no
manual application change was made beyond Ruff's authorized safe output. The two options for
this remaining blocker are to grant the exact one-line manual scope exception (remove
`.replace("Z", "+00:00")` inside `datetime.fromisoformat`), or retain the finding and blocked
status. No rule was disabled and no policy alternative was selected.

The preserved application diff exactly matches `.review/ruff-safe-app.patch`, captured directly
after the safe-fix command. It comprises the six application files listed above. No test assertion
was changed. `pyproject.toml`, `.gitmodules` and the orchestrator's `tools/contract-pin.sh` remain
unchanged. Seven generated bytecode directories from the prior reproduction were removed without
deleting any tracked files. Generated Ruff cache was removed too. Final status contains 173
changed/untracked source/artifact paths, all within the original scope plus the explicitly ruled
header, benchmark and safe-fix extensions; no bytecode cache paths remain. The initial 87-path
count predates the authorized Ruff pass and is historical.

Additional commands since the 20:14 update: independent import/docs/leak/engine/whitespace checks;
progress-log `tail` reads; generated-cache cleanup with a tracked-file guard; application-diff byte
comparison with the saved safe patch; protected-file comparisons; final pin/lint reruns; report
finalization; final path/status, environment-pattern and branch/HEAD audits. These checks did not
alter the clone's history, branch, remotes, tags or Git configuration. Logs are retained in the
ignored `.review/` directory. The accepted reference CI trust model is unchanged.

The initial scope options and color-failure inventory are retained solely as historical command
results. The 20:14 rulings supersede them; only the safe-only FURB162 exception remains unresolved.
