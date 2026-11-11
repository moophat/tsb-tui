# Installer build — phase 8, PR B

**AI-Model:** gpt-6.1-sol

Implemented within the brief and subsequent rulings. Automated installer proofs and lint pass; the full suite had one UI timeout that passed in isolation, and native Windows proof remains outstanding.

## Delivered scope

- Windows per-user installer: manifest schema 1 and zip SHA-256/size validation; offline `-Zip`; process-path refusal; sibling extraction; preserved config; rename swap and the ruled restoration/manual-recovery path; UTF-8 without BOM; unexpanded user PATH stored as `REG_EXPAND_SZ`; setting-change broadcast; unblock and one check-launcher invocation; installed version, path and configured broker reporting.
- Linux fetch stage: CLI/environment/rendered base, manifest or pinned-version checksums, Python JSON parsing, curl/wget, checked release assets and disposable download directory. Local asset directories still install without fetching. Broker comes from CLI/environment or the fetched base's origin; no known broker means no new config and a message naming its path and `--broker`. Existing files, including empty files, are kept. Installer-only environment controls do not leak into application config loading.
- macOS explicitly refuses because its release artifact is pending, as ruled.
- Release uploads include `install.ps1`; `SHA256SUMS` covers all uploaded assets except itself, including the zip's `.sha256` sidecar. The specifically authorized existing release-test changes concern only this asset set, its fixture copy and checksum expectations. No publisher or manifest generator was added.
- README and usage guide document Windows 11/Linux one-liners, explicit-base use, pinned versions, local assets, broker configuration, the browser-download fallback and macOS pending.

## Verification

- **Before-change proof:** ran the new installer pytest file against a disposable copy of `main`'s scripts/workflow (`17a7508`, also this branch's starting HEAD): **15 failed in 10.33s**. Failures cover manifest fetch, pinned-version fetch, corruption before pipx, preserved config, unresolved-token refusal, macOS refusal, checksum/upload coverage, local config/messaging, broker/base precedence, rendered origin and the missing Windows implementation. The Pester wrapper fails there because the installer functions do not exist. Existing local-install and assembler regressions are retained, rather than claimed as new failures on main.
- **Offline CI-image proof:** Docker image built successfully. PowerShell 7.4.13 is pinned to its upstream published SHA-256; Pester 5.7.1 is pinned to the downloaded archive's SHA-256. Neither uses a Microsoft package repository. The build imports Pester. Through `make test`, the image with networking disabled reports **15 passed, 1923 deselected**; those are the selected installer checks, not a full-suite result. HTTP tests use a real loopback server with stub pipx and a transport stub restricted to loopback. The Pester wrapper invokes the retained **13 Windows function tests: 13 passed, 0 failed, 0 skipped**.
- **Lint:** `make lint` with `PYTHON` set to the test venv interpreter passed, including **66 repository/import tests**, ruff, import lint, docs-branch guard, leak check and pinned-engine validation.
- **Packaging regression:** existing `test_assembled_bundle_holds_the_exe_and_never_a_config`: **1 passed**, using disposable inputs.
- **Syntax:** `bash -n install.sh`, PowerShell parser and `git diff --check` passed.
- **Full suite:** final `make test` with the test venv and pinned PowerShell/Pester completed: **1937 passed, 1 failed in 1915.57s**. All 15 installer checks passed. The failure was `tests/test_target_form.py::test_saved_target_is_selected_after_it_leaves_the_query`, waiting 60 seconds for the new row to become selected. Its isolated rerun through `make test` with `PYTEST_ADDOPTS` selecting that test reported **1 passed, 1937 deselected in 10.28s**. No existing TUI production code or target-form test was changed. This is a non-reproducing full-suite timeout, not a green full-suite result. There were no skips in the completed full run.

The initial full-suite baseline was interrupted after a device-name rendering timeout (293 passed, 1 failed). Another in-progress run had loaded the pytest wrapper before the Pester plaintext-output fix and was interrupted after 914 passes and that stale color assertion failure. The completed final run includes that fix and passed all 15 installer checks; these earlier interrupted runs are not claimed as green.

## Limits and checks without a release hash

With `-Zip` and no adjacent `SHA256SUMS`, the installer explicitly warns that no release SHA-256 verification occurs. It checks that extraction succeeds, that there is one versioned Windows bundle directory and that the launcher, check command and bundled interpreter exist. It does not claim authenticity or a release-hash check. With an adjacent checksum file, exactly one matching zip entry is required and its SHA-256 must match. Offline mode makes no network request.

Registry access, native process enumeration, `WM_SETTINGCHANGE`, `Unblock-File`, Windows PowerShell 5.1 execution and actual PC/managed-laptop installation remain unverified on Windows. The Linux Pester proofs cover PATH merge, BOM-free/config-preserving writes, process-path classification and successful/failed/restored swaps; they do not replace the real Windows proof. No staging, lab or workstation installation was attempted.

A scoped read-only simplifier found no required simplification or definite correctness defect. All 13 Pester cases were retained. Final path review found only authorized changes. No `program/` or `.aiworkflow/` modifications, rotation command, commit or push in this continuation. The supplied `BRIEF.md` remains untracked.

Status: DONE_WITH_CONCERNS

## Qodo round 1

**AI-Model:** gpt-6.1-sol

Accepted all three findings under the operator's rulings. Windows no longer accesses the global TLS protocol selector. Both download paths require HTTPS except for the three named loopback hosts, disable redirects, and identify redirect failures. Windows removes failed new trees unless restoration also fails; healthy live installs remove stale siblings with warnings. Missing or incomplete live installs preserve all recovery folders and print their paths and manual recovery commands.

Red-first Makefile installer run: **10 failed, 14 passed, 1923 deselected**. This includes the newly ruled incomplete-live-folder case, TLS policy, URL policy, redirects, and changed swap cleanup expectations. Added a further pre-swap config-copy failure proof. Loopback transport tests exercise curl and wget against actual redirect responses.

Verification: final installer run passed (**26 passed, 1923 deselected in 32.96s**), including **21 Pester cases** and real PowerShell, curl and wget loopback redirect refusals. Lint passed, including ruff and **66 repository/import tests**. The full Makefile suite passed: **1949 passed in 1837.20s (30m 37s)**, with no skips or failures. The previously timing-out target-form case passed in this run. No commit, push, tracker rotation or changes under the workflow directory.

The final lint rerun passed after all source and test edits, including the engine pin check. Bash syntax and the final whitespace/path review passed. Only the two installers, their pytest/Pester tests and this report changed. No commit or push was made; the workflow directory was untouched.

Native Windows 11 PowerShell 5.1/7 TLS negotiation and registry/broadcast integration remain unverified; Linux PowerShell 7 tests do not replace that platform proof. All requested automated checks completed.

Qodo round 1 status: implemented and automated verification passed; native Windows proof remains outstanding.
