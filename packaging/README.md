# Windows x64 zip assembler

Requires Linux, Python 3.12+, pip, git, tar support and the system
`zip` tool. No target code runs on the build machine.

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
PyPI's release metadata and the downloaded bytes. The requirements file pins the
complete Windows CPython 3.12 dependency closure, including ABI3 wheels accepted
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

Tests cover cached-input corruption, all three engine-selection cases, missing
dependency licences, archive layout, CRLF launchers, bytecode exclusion, the
repository's leak pattern and the output checksum. The leak scan covers only the
two launchers, README.txt, VERSION, lib/engine/ENGINE and the tsb_tui,
tsb_contract_client and tsbc package trees under lib/site. Third-party trees are
excluded; a regression test proves a planted leak in tsb_tui fails the scan.
Dummy engines prove assembly
only; run `tsb-tui-check.cmd` with the real engine on Windows for runtime proof.
