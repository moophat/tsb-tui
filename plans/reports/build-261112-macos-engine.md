# macOS TUI engines — 2026-11-12

AI-Model: codex
Status: DONE_WITH_CONCERNS

Built both targets once on the integration node with the pinned Ghostty commit,
Zig 0.16.0, digest-pinned image, `ReleaseFast`, `-j1`, and one visible CPU.
No SDK, toolchain switch, upstream patch, signing step, or reproducibility claim
was used. The temporary node directory was removed and no engine unit remains
active.

Commands:

```text
taskset -c <first-allowed-cpu> zig build -j1 -Demit-lib-vt -Doptimize=ReleaseFast -Dtarget=aarch64-macos -Dversion-string=1.3.2-dev+34f3900 --prefix /build/prefix
taskset -c <first-allowed-cpu> zig build -j1 -Demit-lib-vt -Doptimize=ReleaseFast -Dtarget=x86_64-macos -Dversion-string=1.3.2-dev+34f3900 --prefix /build/prefix
```

Arm64 Zig wall time was 517 seconds (unit: 575 seconds); x86_64 was 558 seconds (unit: 618 seconds). The staged
artifacts contain one regular dylib, headers and licenses. Versioned dylib
symlinks were logged and are not shipped. Final dylib hashes and sizes are:

```text
macos-arm64/libghostty-vt.dylib  7adbb183f5e79b383a02f060dda1804890bae6d703a2fc95546b3849888d0d26  2195512
macos-x64/libghostty-vt.dylib    f0256277623fb4236f4623b518fc6ff6fa8feb9152fa0b249abccfe75d86deae  2370391
```

The node and local tarball hashes matched after base64 transfer:
`c8a4c3041c097201751df0333bbc9ebd28c938cd6df20a6abea2815a3b63c87f` (arm64)
and `401dc8b0ba2acaccb31a37a4674b6bdc90f9801b8d22da1184a5a799c6ac6f70`
(x86_64). The macOS license trees include simdutf, highway, wuffs and uucode
files, with the simdutf files hash-pinned from the Windows precedent.

Checker output:

```text
Mach-O arm64: cputype 0x0100000c, minos 13.0.0, signature present
LC_ID_DYLIB @rpath/libghostty-vt.dylib
LC_LOAD_DYLIB /usr/lib/libSystem.B.dylib
Mach-O x86_64: cputype 0x01000007, minos 13.0.0, signature absent
LC_ID_DYLIB @rpath/libghostty-vt.dylib
LC_LOAD_DYLIB /usr/lib/libSystem.B.dylib
```

Verification: `python3 -m pytest -q -p no:cacheprovider packaging/engine/tests`
reported **74 passed**. `make lint` exited **0** (80 repository tests passed).
The real-Mac load through `tsb_tui.term.ghostty.load` and ABI manifest check is
still owed.

Concern: GNU nm emitted unsupported-format diagnostics while returning success.
This caused the initial packaging pass to omit simdutf, although the journal
listed simdutf.o. The final script uses named archive members for macOS evidence,
consistent with the existing license rule. After compilation, both tarballs
were corrected with the Windows precedent's hash-pinned simdutf licenses and
INDEX entry; no dylib bytes changed and no compile was repeated. The corrected
script's complete Docker path was not rerun; that would violate one build per
target. The corrected license trees and all shipped bytes passed the pin tests.

No build failure or unspecified design choice caused a stop. No commits, pushes,
git-config edits, plans or ledger edits were made. Scope check showed only
packaging/engine/ and this report. Verification ran before final prose edits.
