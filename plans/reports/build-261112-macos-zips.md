# macOS zip assembler report

AI-Model: gpt-6.1-sol

Status: DONE_WITH_CONCERNS

## Built

Added `macos-arm64` and `macos-x64` targets to the existing assembler and target-aware locked-input verification. Both use vendored engines without an operator engine argument. Added LF POSIX launchers with portable symlink resolution and matching Windows environment variables (including the engine directory prepended to PATH), target requirements, quarantine instructions, and release build/upload wiring. `install.sh` is unchanged.

The release path retains clean-tree and exact-tag checks. The authorized harness, `packaging/tests/test_macos_build.py`, answers only the tracked-status and exact-tag git queries, supplying `v0.0.0`. Other git queries, contract-pin validation, extraction, pip operations, notice collection and system zip run normally. It uses the real input cache, assembles into pytest temporary directories and skips naming a missing locked input instead of downloading. Corrupt inputs fail. Built archives and checksums were copied into ignored `packaging/out/` for review.

## Pinned inputs

CPython 3.12.16 uses PBS release 20261026, install_only:

| Target | PBS architecture | Bytes | SHA256 |
|---|---|---:|---|
| macos-arm64 | aarch64-apple-darwin | 25172086 | fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e |
| macos-x64 | x86_64-apple-darwin | 24873291 | a962e47df522889eaa7ff0d6a56203f1079f68aabb65ffdbe8455f362bc769f3 |

Each runtime digest was read from that PBS release's SHA256SUMS and compared with SHA256 of the downloaded bytes. Byte sizes were measured and recorded in `packaging/inputs.lock`.

Both target requirements retain the Windows versions: argon2-cffi 25.1.0, argon2-cffi-bindings 25.1.0, cffi 2.1.1, linkify-it-py 2.2.0, markdown-it-py 4.2.0, mdit-py-plugins 0.6.1, mdurl 0.1.2, platformdirs 4.14.5, precis_i18n 1.1.2, pycparser 3.12, Pygments 2.21.0, rich 15.0.0, textual 8.2.8 and typing_extensions 4.16.0. Setuptools 84.2.1 remains the host-only build input. All URLs, sizes and hashes are in `packaging/inputs.lock`; each target requirements file pins its selected wheel hash.

For every selected macOS wheel, the filename, URL, size and SHA256 were matched to PyPI's version-specific JSON metadata, then the downloaded file's length and SHA256 were checked. Pip's binary-only cross-platform download proved the closure. Tags: `macosx_11_0_arm64` and `macosx_10_15_x86_64`; cffi's Intel wheel requires the latter rather than 10.13. Both vendored engines require macOS 13. No version was moved and no sdist was used.

`packaging/verify-inputs.sh --fetch` then filled/checked the cache through the production verifier: `Inputs verified: 48`.

## Artifacts

| Archive in packaging/out | Bytes | SHA256 |
|---|---:|---|
| tsb-tui-0.0.0-macos-arm64.zip | 45018471 | 6c76b09d371a0224d9c829936d6ec7d597922cbd27781d6620d8b1b747b8d325 |
| tsb-tui-0.0.0-macos-x64.zip | 44534888 | 0ace2ccb4adc4bd37091897b9ba3e6a5829233191904e078286ed423c9c3cd7a |

Each archive has an adjacent `.zip.sha256`. The test version is 0.0.0, not a release tag.

## Mach-O facts

`python3 -I packaging/engine/check-macho.py` inspected each extracted zip engine without loading it:

```text
Mach-O arm64: cputype 0x0100000c, minos 13.0.0, signature present
LC_ID_DYLIB @rpath/libghostty-vt.dylib
LC_LOAD_DYLIB /usr/lib/libSystem.B.dylib
Mach-O x86_64: cputype 0x01000007, minos 13.0.0, signature absent
LC_ID_DYLIB @rpath/libghostty-vt.dylib
LC_LOAD_DYLIB /usr/lib/libSystem.B.dylib
```

Reading the Mach-O header of each zip's `python/bin/python3.12` gives arm64 cputype `0x0100000c` and Intel cputype `0x01000007`. No macOS code ran. The arm64 signature is already present in the vendored engine; this job did no signing or notarisation.

## Verification and resolved blocker

The targeted real-build harness reported `2 passed in 38.78s`.
The final plain `python3 -m pytest packaging/tests/ -p no:cacheprovider -q` reported `51 passed, 2 skipped in 62.86s`. The two skips are the existing mingw-w64 launcher determinism and PE/import tests because the host has no cross-compiler. A Windows launcher was built locally in a disposable Linux container with the compiler versions pinned by the CI Dockerfile, allowing the real Windows assembly fallback to run. No builds or tests ran on another machine.

macOS coverage includes zip layout, sorted members, zipinfo and ZIP mode 755 on interpreter/launchers, LF launchers, no bytecode/cache trees, authored-text leak checks using the repository pattern, checksums, engine headers and interpreter cputypes. Corrupt-input tests cover both macOS target lock rows. Linux shell-stub tests cover both launchers directly, through relative/absolute symlink hops in another directory, and from a folder with spaces, checking environment overrides and unchanged arguments (including empty arguments and newlines).

The first `make lint` exited 2: `100 passed, 2 failed`. The failures are the release-workflow tests in `.github/scripts/tests/test_release_install.py`, which hard-code the Windows-only stage and asset set. Restoring the existing Windows stage name resolves the first assertion but the fixture still supplies only Windows assets and expects only Windows assets in checksum/upload results.

The orchestrator authorized `.github/scripts/tests/test_release_install.py` on 2026-11-12. Its fixture now supplies both macOS zips and their checksum files, and strict assertions require the exact asset set, exact checksum lines and exact published payload set in both release creation and upload paths. Missing, extra or unchecked assets continue to fail. The Intel `macosx_10_15_x86_64` wheel tag is explicitly accepted and documented in `packaging/README.md`.

After that authorization, verification ran once each:

- `make lint`: exit code **0**; `102 passed in 68.82s`. Import lint, repository leak check and vendored engine checks also passed.
- `python3 -m pytest packaging/tests/ -p no:cacheprovider -q`: exit code **0**; `51 passed, 2 skipped in 65.13s`. Both macOS end-to-end builds and all POSIX launcher cases passed. The two pre-existing Windows cross-compiler tests remain skipped because mingw-w64 is absent on the host; those checks were not verified here.

No blocking design or scope decision remains. Status is DONE_WITH_CONCERNS because of those explicitly reported Windows test skips and the earlier verification-run deviation.

Deviation from the requested once-only verification: the full packaging suite was invoked twice, and lint was invoked a second time after restoring the workflow stage name. The final packaging result above is the captured result; the first suite summary was not retained. This report does not claim once-only execution.

No commits, pushes, git configuration changes, plan edits or ledger edits were made. `git diff --check` passes. Changed paths are limited to `packaging/`, `.github/workflows/release.yml`, the explicitly authorized `.github/scripts/tests/test_release_install.py` and this report. A real Mac runtime proof and live release publishing remain unverified as expected for this Linux-only task.
