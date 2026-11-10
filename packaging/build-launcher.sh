#!/usr/bin/env bash
# Build packaging/cache/tsb-tui.exe with mingw-w64. Run inside the CI image, which pins the compiler.
set -euo pipefail
umask 022
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/launchers"
mkdir -p "$here/cache"
x86_64-w64-mingw32-gcc -O2 -s -municode -static -Wl,--no-insert-timestamp -Wl,--build-id=none \
    -ffile-prefix-map="$here/launchers=." tsb-tui.c cmdline.c -o "$here/cache/tsb-tui.exe"
