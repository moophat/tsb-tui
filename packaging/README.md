# Windows x64 and macOS zip assembler

Requires Linux, Python 3.12+, pip, git, tar support and the system
`zip` tool. No target code runs on the build machine.

The zip's `tsb-tui.exe` is built from `launchers/tsb-tui.c` and `launchers/cmdline.c` with the mingw-w64
cross-compiler pinned in `.github/ci/py.Dockerfile`, so build it inside that image (the release workflow does):

```sh
packaging/build-launcher.sh   # writes cache/tsb-tui.exe; the same sources give the same bytes
```

The assembler stops if `cache/tsb-tui.exe` is missing. `tsb-tui.cmd` stays in the zip beside the exe for now.

Build from a version-tagged checkout (the existing release policy stamps the
wheel and VERSION from the tag at HEAD):

```sh
capped -- packaging/build-zips.sh windows-x64 --engine engine-input
```

The engine argument is a directory or tar archive containing DLLs and
`licenses/INDEX`. Multiple DLLs require a root `ENGINE` file with exactly one line:
the main DLL's filename. The assembler always writes `lib/engine/ENGINE`.
The supplied engine is an operator input, separate from the locked downloads.

`inputs.lock` records kind, target, URL, SHA256 and byte size. Files are cached by
SHA256. Missing files are fetched; present files with mismatched size or hash fail
with the lock line number. To verify without downloading:

```sh
packaging/verify-inputs.sh
```

CPython 3.12.16 comes from PBS release 20261026; its hash was taken from that
release's SHA256SUMS and checked locally. Wheel hashes were checked against
PyPI's release metadata and the downloaded bytes. Each target requirements file pins the
complete CPython 3.12 dependency closure, including ABI3 wheels accepted
by CPython 3.12. No source distributions are accepted. Setuptools is a separately pinned host-only
build input, installed into the disposable build directory.

Build output is `out/tsb-tui-<version>-windows-x64.zip` and `.zip.sha256`.
`SOURCE_DATE_EPOCH` controls mtimes, defaulting to HEAD's commit timestamp.
The archive retains the PBS licence files and copies the engine licence tree;
THIRD_PARTY_NOTICES concatenates the engine index and dependency licence files.
TSB's first-party licence decision remains outside this track.

Validation sequence:

1. Create a dummy DLL and a test-only `licenses/INDEX` under `cache/dummy-engine/`.
2. Run the full build above with that engine directory.
3. Run `python -m pytest packaging/tests/ -p no:cacheprovider`.
4. Run `make lint`.

`tests/test_launcher.py` splits command lines with the host C compiler, and inside the CI image checks that two
launcher builds are byte-identical and that the exe is a static x64 console program importing only system DLLs.
Tests cover cached-input corruption, all three engine-selection cases, missing
dependency licences, archive layout, CRLF launchers, bytecode exclusion, the
repository's leak pattern and the output checksum. The leak scan covers only the
two launchers, README.txt, VERSION, lib/engine/ENGINE and the tsb_tui,
tsb_contract_client and tsbc package trees under lib/site. Third-party trees are
excluded; a regression test proves a planted leak in tsb_tui fails the scan.
Dummy engines prove assembly
only; run `tsb-tui-check.cmd` with the real engine on Windows for runtime proof.

## macOS zips

Build either Apple target on Linux with `packaging/build-zips.sh macos-arm64` or
`packaging/build-zips.sh macos-x64`. The engine is taken from the vendored
`engine/macos-arm64` or `engine/macos-x64` directory; no operator engine input is
used. The archive contains POSIX launchers and the PBS CPython runtime. The
bundles are not release-signed or notarised. For a zip downloaded in a browser,
clear quarantine after extraction with `xattr -dr com.apple.quarantine <folder>`;
files downloaded with `curl` carry no quarantine flag.

The macOS wheel tags are `macosx_11_0_arm64` and `macosx_10_15_x86_64` (the
Intel cffi wheel needs 10.15), accepted by the orchestrator on 2026-11-12.
Both engines require macOS 13 or later. Outputs
are `out/tsb-tui-<version>-macos-arm64.zip` and `-macos-x64.zip`, each with a
`.zip.sha256`. Execute permissions are retained on `python/bin/python3*` and
both launchers. Launchers resolve symlinks, including links from a PATH folder.
`install.sh` installs these zips on macOS, and both launchers clear the macOS download
quarantine flag from their own folder before starting Python. Neither has had a real Mac run yet.

For an untagged or modified development checkout, fill the cache explicitly
with `packaging/verify-inputs.sh --fetch`, then run
`python3 -m pytest packaging/tests/test_macos_build.py -p no:cacheprovider -q`.
The end-to-end harness answers only the clean-status and exact-version-tag git
queries (`v0.0.0`), and runs real assembly into pytest temporary directories.
It never downloads: it skips naming any missing pinned input. Corrupt cached
inputs fail. The release assembler keeps its clean-tree and tag requirements.
The macOS tests inspect Mach-O headers without executing target code, exercise
launchers on Linux with a shell interpreter stub, and check zip structure,
modes with zipinfo, LF launchers, bytecode exclusion, authored-text leaks and
checksums. Third-party text is excluded from the leak check, as for Windows.
