# Fix report: unpin libc6-dev in the CI image (#56)

**AI-Model:** strata

## Change

`.github/ci/py.Dockerfile` pinned `libc6-dev=2.36-9+deb12u14` while the base image is pinned by digest and
`apt-get update` reads the live bookworm archive, so the next libc security update retires that exact version and
breaks the image build. Per the ruling, `libc6-dev` is now taken from the archive without a version (as #53 did for
`curl` and `wget`), with a comment saying why. Every mingw-w64 pin (`gcc-mingw-w64-x86-64-win32`,
`binutils-mingw-w64-x86-64`, `mingw-w64-x86-64-dev`) and every other pin (`gcc`, `gcc-12`) is unchanged.

```diff
-        gcc=4:12.2.0-3 gcc-12=12.2.0-14+deb12u1 libc6-dev=2.36-9+deb12u14 \
+        gcc=4:12.2.0-3 gcc-12=12.2.0-14+deb12u1 libc6-dev \
```

## The release build's reproducibility check

The Windows launcher `tsb-tui.exe` is built by `packaging/build-launcher.sh` with the pinned cross toolchain:

```
x86_64-w64-mingw32-gcc -O2 -s -municode -static -Wl,--no-insert-timestamp -Wl,--build-id=none \
    -ffile-prefix-map="$here/launchers=." tsb-tui.c cmdline.c -o "$here/cache/tsb-tui.exe"
```

The reproducibility check is `packaging/tests/test_launcher.py`:

- `test_build_is_deterministic` (test_launcher.py:71-75) runs `build()` twice, each building `cache/tsb-tui.exe`
  from the same sources, and asserts the two SHA-256 hashes are equal. It compares the launcher's bytes across two
  rebuilds in the same image.
- `test_executable_is_a_static_x64_console_program` (test_launcher.py:88-103) reads the PE imports with
  `x86_64-w64-mingw32-objdump` and asserts they are only `kernel32.dll`, `msvcrt.dll` and `api-ms-win-*`, and that
  no `libgcc`, `libwinpthread` or `libstdc++` DLL is imported.

The release job then hashes the produced zip: `packaging/assemble.py` copies `cache/tsb-tui.exe` into the bundle and
writes `out/...-windows-x64.zip.sha256`; `test_packaging.py::test_built_zip` re-hashes the zip against that checksum
and checks the launcher entry starts with `MZ`.

## Why an unpinned host libc6-dev cannot change the launcher's bytes

The launcher is compiled by the cross-compiler `x86_64-w64-mingw32-gcc`, whose packages are all pinned
(`gcc-mingw-w64-x86-64-win32`, `binutils-mingw-w64-x86-64`, `mingw-w64-x86-64-dev`). `-static` links the C runtime
from the pinned mingw-w64 target library (`mingw-w64-x86-64-dev`), not the host glibc; `-municode` uses mingw's own
Unicode entry point. The import assertions prove the output carries no host C runtime (no `libgcc`,
`libwinpthread`, `libstdc++`). `libc6-dev` is the host Linux glibc headers/libs, used only for host-side tooling —
e.g. the `split` fixture compiles `cmdline.c` into a `.so` with the host `gcc` to test the command-line split — and
is never linked into a Windows PE. The determinism flags (`--no-insert-timestamp`, `--build-id=none`,
`-ffile-prefix-map`) strip the remaining non-determinism, so the bytes come from the pinned toolchain and the sources
alone. Nothing in the check shows the launcher's bytes depend on the host libc6-dev, so the snapshot.debian.org
option is not the one to weigh here.

## Verification

Full `make lint` run in this worktree: 108 passed, 2 failed, exit non-zero. The 2 failures are
`.github/scripts/tests/test_release_install.py::test_release_checksums_archive_and_both_upload_paths[yes|no]`, each a
`FileNotFoundError: program/contract` — the pinned private `program/` submodule is not populated here.

The failures are independent of this change: the tests read `release.yml`, `vendor/`, `tools/` and `program/contract`,
never the Dockerfile. On a clean tree (`git stash`) the same 2 tests fail with the identical
`FileNotFoundError`, so the gate is red on the environment, not on this diff.

`make contract-init` cannot populate `program/` here: the submodule resolves to
`git@github.com:moophat/tsb-program.git` (private) and host-key verification fails with no deploy key, and the local
sibling `../tsb-program/` has no `srs`/`contract`. CI populates `program/` with `PROGRAM_DEPLOY_KEY` and runs
`make contract-init && make contract-check` **before** `make lint` (test.yml:136-176), so `test_release_install.py`
passes in CI. The image build itself is done by CI, not here.

Status: DONE_WITH_CONCERNS
Summary: Unpinned `libc6-dev` in `.github/ci/py.Dockerfile` per the ruling, keeping every mingw-w64 and other pin; the release reproducibility check shows the launcher's bytes come from the pinned cross toolchain and sources, not host glibc.
Concerns/Blockers: `make lint` cannot reach exit 0 in this worktree because the private `program/` submodule is unpopulated (no credentials); the 2 failures are pre-existing on clean main and pass in CI, which populates `program/` before lint. No commit, no push.
