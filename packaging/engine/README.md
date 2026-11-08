# Windows x64 terminal engine

Built once on the integration-node role on 2026-11-08 (Asia/Saigon).
AI-Model: codex. This directory supplies the engine for the Windows TUI zip;
the existing Linux engine and TUI loader are unchanged.

Inputs come from [`vendor/libghostty-vt/PIN`](../../vendor/libghostty-vt/PIN):

- Ghostty commit `34f39002c6e3974b54e6a6d400bd83777c5ea558`.
- Zig `0.16.0`, archive SHA256
  `70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00`.
- Image `golang:1.26.9-bookworm@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c`.

The script reads that PIN first. If it lacks required build inputs, it reads
`vt/third_party/libghostty-vt/PIN` in the sibling tsb-core checkout instead;
`TSB_CORE_ROOT` can select that checkout. This build needed no fallback.
Commit and Zig version changes require reviewing the license pins; unsupported
targets and mismatched inputs fail.

## Build and outputs

In Docker, with the work directory fixed to `/build`, the build ran:

```sh
taskset -c <first-allowed-cpu> zig build -j1 -Demit-lib-vt \
  -Doptimize=ReleaseFast -Dtarget=x86_64-windows-gnu \
  -Dversion-string=1.3.2-dev+34f3900 --prefix /build/prefix
```

CPU affinity exposed exactly one CPU to Zig; Docker was also capped at one CPU.
The Zig archive checksum, compiler version, and checked-out source commit were
verified before building. Zig took **1,048 seconds (17m 28s)**; setup plus build
took **1,094 seconds (18m 14s)**. There was one compile run, with no upstream
patches, MSVC, normalization, or reproducibility claim.

The installed runtime was `bin/ghostty-vt.dll` (2,209,280 bytes), with import
library `lib/ghostty-vt.lib` (55,264 bytes). Both are in
[`windows-x64/`](windows-x64/). The build also installed `bin/ghostty-vt.pdb`
(7,393,280 bytes), `lib/ghostty-vt-static.lib` (11,287,344 bytes), 35 headers,
and the shared/static pkg-config files. Those auxiliary files are not vendored
here. The build script's output tar includes the DLL, import library, headers,
and licenses, plus a separate tar SHA256 file.

[`build-engine.sh`](build-engine.sh) codifies that build and the post-build
license collection performed on the completed run. It accepts a target argument;
only `x86_64-windows-gnu` is qualified in this cut. As in tsb-core's build script,
it requires `TSB_VT_BUILD_HOST_ROLE=integration-node`. Set that role only on the
integration node, never on a workstation. It checks active build/loop services,
holds a node lock, uses a fresh temporary directory, and removes its container
and temporary directory on exit. Every remote command must go through the fleet
shell's `run` verb, using the integration-node address from the ops environment
document. On that node, launch in the background with systemd, for example:

```sh
run=$(date +%Y%m%d%H%M%S)
unit=tsb-engine-win-$run
systemd-run --unit "$unit" --collect --property=Type=exec \
  --setenv=TSB_VT_BUILD_HOST_ROLE=integration-node \
  --setenv=TSB_ENGINE_BUILD_UNIT="$unit.service" \
  --setenv=TSB_ENGINE_BUILD_OUT="$PWD/engine-output" \
  bash "$PWD/packaging/engine/build-engine.sh" x86_64-windows-gnu
```

Check that no post-merge loop is active first and wait if one is running.
Inspect the unit with `systemctl` and `journalctl`; do not start a second build.
The script emits `libghostty-vt-<commit>-x86_64-windows-gnu.tar` and `.tar.sha256`.
Transfer artifacts using fleet-shell `run` with `base64 -w0`, decode locally,
and compare node/local SHA256 before replacing pins. This run's DLL and import
library were transferred that way; all license hashes were also compared.

The licenses were collected from the actual pinned source, fetched packages,
and verified Zig SDK using the Linux build's component-evidence rules and
hash-checked supplemental licenses. [`licenses/INDEX`](windows-x64/licenses/INDEX)
records the components. `licenses/zig/mingw-COPYING` additionally covers Zig's
bundled Windows runtime support. Every shipped file's SHA256, size, and target
are recorded in [`ENGINE-PINS`](ENGINE-PINS).

## Imported DLLs

`objdump -p` on the integration node reported these `DLL Name` entries:

```text
ntdll.dll
KERNEL32.dll
api-ms-win-crt-heap-l1-1-0.dll
api-ms-win-crt-runtime-l1-1-0.dll
api-ms-win-crt-environment-l1-1-0.dll
api-ms-win-crt-private-l1-1-0.dll
api-ms-win-crt-stdio-l1-1-0.dll
api-ms-win-crt-string-l1-1-0.dll
```

No `libgcc` or `libwinpthread` DLL was imported. These imports still need to
resolve on the operator's Windows PC; this Linux structural check does not
prove that Windows can load the library or pass the TUI's ABI manifest check.
That OS-level proof remains for the operator's Windows run through
`tsb_tui.term.ghostty.load` and `tsb_tui.app.load_engine`.

## Verification

```sh
python3 -I packaging/engine/check-pe.py packaging/engine/windows-x64/ghostty-vt.dll
python3 -m pytest -q -p no:cacheprovider packaging/engine/tests
make lint
```

The PE checker verifies the DOS/PE headers, machine `0x8664`, and DLL flag without
loading the library. Tests verify exact file coverage, SHA256, byte size, target,
and rejection of damaged or wrong-architecture DLLs. `make lint` currently
covers the repository's existing gates; run the packaging tests separately.
