#!/usr/bin/env bash
# Build the pinned Windows or macOS engine on the integration node. AI-Model: codex
set -euo pipefail
fail() { echo "build-engine.sh: $*" >&2; exit 1; }
note() { echo "build-engine.sh: $*"; }

check_active_units() {
  local units err_file err status=0 conflict
  err_file="$(mktemp)"
  units="$(systemctl list-units 'tsb-pml*.service' 'tsb-engine*.service' --state=active,activating,reloading --no-legend --plain 2>"$err_file")" || status=$?
  err="$(cat "$err_file" 2>/dev/null || true)"
  rm -f "$err_file"
  [ "$status" = 0 ] || fail "failed to query systemctl for active units (exit status $status)${err:+: $err}"
  if [ -n "$units" ]; then
    conflict="$(printf '%s\n' "$units" | awk -v current="${TSB_ENGINE_BUILD_UNIT:-}" '
      !$1 || $1 == current { next }
      {
        state = ($3 ~ /^(active|activating|reloading)$/) ? $3 : ($2 ? $2 : "unknown")
        item = $1 " (" state ")"
        out = (out ? out ", " : "") item
      }
      END { if (out) print out }
    ')"
    [ -z "$conflict" ] || fail "another node build or post-merge loop is running: $conflict"
  fi
}

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0 2>/dev/null || true
fi

inside=0
if [ "${1:-}" = --inside ]; then inside=1; shift; fi
TARGET="${1:?usage: build-engine.sh TARGET}"
[ "$#" = 1 ] || fail "expected exactly one target"
case "$TARGET" in x86_64-windows-gnu|aarch64-macos|x86_64-macos) ;; *) fail "unsupported target" ;; esac
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
pin="${TSB_ENGINE_PIN:-$repo/vendor/libghostty-vt/PIN}"
required=(upstream_url upstream_commit upstream_version zig_version zig_url zig_sha256 build_image build_optimize build_workdir)
complete_pin() {
  local key
  [ -f "$1" ] || return 1
  for key in "${required[@]}"; do
    grep -Eq "^${key}: .+" "$1" || return 1
  done
}
if ! complete_pin "$pin"; then
  pin="${TSB_CORE_ROOT:-$repo/../tsb-core}/vt/third_party/libghostty-vt/PIN"
fi
complete_pin "$pin" || fail "no complete engine PIN (set TSB_CORE_ROOT for fallback)"
value() { sed -n "s/^$1: //p" "$pin"; }
GHOSTTY_URL="$(value upstream_url)"
GHOSTTY_COMMIT="$(value upstream_commit)"
ZIG_VERSION="$(value zig_version)"
ZIG_URL="$(value zig_url)"
ZIG_SHA256="$(value zig_sha256)"
IMAGE="$(value build_image)"
OPTIMIZE="$(value build_optimize)"
VERSION="$(value upstream_version)+${GHOSTTY_COMMIT:0:7}"
[[ "$GHOSTTY_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "invalid upstream commit"
[[ "$ZIG_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "invalid Zig sha256"
[[ "$IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || fail "build image must be digest-pinned"
[ "$(value build_workdir)" = /build ] || fail "PIN requires a different build directory"
[ "$OPTIMIZE" = ReleaseFast ] || fail "PIN requires a different optimization level"
[ "$ZIG_VERSION" = 0.16.0 ] || fail "review license collection before changing Zig"
[ "$GHOSTTY_COMMIT" = 34f39002c6e3974b54e6a6d400bd83777c5ea558 ] ||
  fail "review license pins before changing upstream"

if [ "$inside" = 0 ]; then
  [ "${TSB_VT_BUILD_HOST_ROLE:-}" = integration-node ] ||
    fail "refusing to run outside the integration-node build role"
  check_active_units
  exec 9>/run/lock/tsb-engine-build.lock
  flock -n 9 || fail "another engine build holds the node lock"
  out="${TSB_ENGINE_BUILD_OUT:-$PWD}"
  mkdir -p "$out"
  out="$(cd "$out" && pwd)"
  work="$(mktemp -d /tmp/tsb-engine-${TARGET%%-*}-XXXXXXXX)"
  container="tsb-engine-build-$$"
  cleanup() { docker stop "$container" >/dev/null 2>&1 || true; rm -rf "$work"; }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  docker run --rm --name "$container" --cpus=1 \
    -v "$repo":/in:ro -v "$pin":/engine-pin:ro \
    -v "$work":/build -v "$out":/out -w /build \
    -e TSB_ENGINE_PIN=/engine-pin \
    "$IMAGE" bash /in/packaging/engine/build-engine.sh --inside "$TARGET"
  exit
fi
[ -f /.dockerenv ] && [ "$PWD" = /build ] || fail "--inside requires the build container"
cd /build
export ZIG_GLOBAL_CACHE_DIR=/build/zig-global ZIG_LOCAL_CACHE_DIR=/build/zig-local LC_ALL=C TZ=UTC
curl -fsSL --retry 3 -o zig.tar.xz "$ZIG_URL"
echo "$ZIG_SHA256  zig.tar.xz" | sha256sum -c -
python3 -I -c 'import tarfile; tarfile.open("zig.tar.xz").extractall("/build/zig-sdk")'
ZIG_DIR="/build/zig-sdk/zig-x86_64-linux-${ZIG_VERSION}"
ZIG="$ZIG_DIR/zig"
[ "$("$ZIG" version)" = "$ZIG_VERSION" ] || fail "Zig version differs from PIN"
git init -q ghostty
git -C ghostty remote add origin "$GHOSTTY_URL"
git -C ghostty fetch -q --depth 1 origin "$GHOSTTY_COMMIT"
git -C ghostty checkout -q --detach FETCH_HEAD
[ "$(git -C ghostty rev-parse HEAD)" = "$GHOSTTY_COMMIT" ] || fail "commit differs from PIN"
app_version="$(sed -n 's/^[[:space:]]*\.version = "\(.*\)",$/\1/;T;p;q' ghostty/build.zig.zon)"
min_zig="$(sed -n 's/^[[:space:]]*\.minimum_zig_version = "\(.*\)",$/\1/p' ghostty/build.zig.zon)"
[ "$app_version+${GHOSTTY_COMMIT:0:7}" = "$VERSION" ] || fail "source version differs from PIN"
[ "$min_zig" = "$ZIG_VERSION" ] || fail "source Zig version differs from PIN"
first_cpu="$(awk '/^Cpus_allowed_list:/ { print $2 }' /proc/self/status)"
first_cpu="${first_cpu%%[-,]*}"
[[ "$first_cpu" =~ ^[0-9]+$ ]] || fail "cannot determine first allowed CPU"
echo "CPUs visible to Zig: $(taskset -c "$first_cpu" nproc)"
started=$SECONDS
(cd ghostty && taskset -c "$first_cpu" "$ZIG" build -j1 -Demit-lib-vt \
  "-Doptimize=$OPTIMIZE" "-Dtarget=$TARGET" "-Dversion-string=$VERSION" --prefix /build/prefix)
echo "zig build wall time: $((SECONDS - started)) seconds"
find /build/prefix -type f -printf '%P %s bytes\n' | sort
if [[ "$TARGET" == *-macos ]]; then
  mapfile -t dylibs < <(find /build/prefix -type f -iname 'libghostty-vt*.dylib')
  [ "${#dylibs[@]}" = 1 ] || fail "expected one installed macOS dylib"
  find /build/prefix -type l -iname '*ghostty-vt*.dylib' -printf 'symlink %P -> %l\n'
  stage=/build/stage
  mkdir -p "$stage/lib"
  cp "${dylibs[0]}" "$stage/lib/libghostty-vt.dylib"
  cp -R /build/prefix/include "$stage/include"
  # macOS shared libraries do not ship an import library.
else
  mapfile -t dlls < <(find /build/prefix -type f -iname '*.dll')
  [ "${#dlls[@]}" = 1 ] || fail "expected one installed Windows DLL; stop without patching upstream"
  objdump -p "${dlls[0]}" | sed -n '/DLL Name/p'
  stage=/build/stage
  mkdir -p "$stage/lib"
  cp "${dlls[0]}" "$stage/lib/"
  cp /build/prefix/lib/ghostty-vt.lib "$stage/lib/"
  cp -R /build/prefix/include "$stage/include"
fi
# The static archive supplies component evidence; it is not shipped in the TUI.
archive=/build/prefix/lib/ghostty-vt-static.lib
if [[ "$TARGET" == *-macos ]]; then archive=/build/prefix/lib/libghostty-vt.a; fi
"$ZIG" ar t "$archive" > /build/member-names.txt
if [[ "$TARGET" == *-macos ]]; then
  # GNU nm cannot read Mach-O objects. Named archive members are component
  # evidence under package_evidence below, including vendored simdutf.o.
  cp /build/member-names.txt /build/nm-all.txt
else
  nm -A "$archive" > /build/nm-all.txt
fi
n_simdutf="$(grep -ci simdutf /build/nm-all.txt || true)"
n_hwy="$(grep -ci hwy /build/nm-all.txt || true)"
n_wuffs="$(grep -ci wuffs /build/nm-all.txt || true)"

SIMDUTF_COMMIT="ca7acbcea967b5dcbab490066e99e3a6e6925539"
UUCODE_COMMIT="9d55524551411b493cca41ca06363625d90aff1e"
PINNED_LICENSE_FILES=(
  "simdutf|LICENSE-MIT|https://raw.githubusercontent.com/simdutf/simdutf/${SIMDUTF_COMMIT}/LICENSE-MIT|fc8dbc04e03ad4efc08a647ffe7f995b811a95bc04c0e85a56d5277c6593fa5f"
  "simdutf|LICENSE-APACHE|https://raw.githubusercontent.com/simdutf/simdutf/${SIMDUTF_COMMIT}/LICENSE-APACHE|3d34610fc6b5e1b0bfe4e2f36171c2d62c28ef05cb8d704f5a0073be41a43b3d"
  "uucode|LICENSE_Bjoern_Hoehrmann|https://raw.githubusercontent.com/jacobsandlund/uucode/${UUCODE_COMMIT}/licenses/LICENSE_Bjoern_Hoehrmann|de219cece932aad5a817bf763393d8d149d378a15d2ad5320e3331eac07626dd"
  "uucode|LICENSE_unicode|https://raw.githubusercontent.com/jacobsandlund/uucode/${UUCODE_COMMIT}/licenses/LICENSE_unicode|1eda5a3b026870c737b22e8bcd4954338612c790db688242e003f41a4fa95175"
)
SIMDUTF_VERSION_PINNED="9.0.0"   # the version the pinned simdutf licence files belong to

package_info() { # hash -> "name<TAB>url"
  local h="$1" f info
  while IFS= read -r f; do
    info="$(awk -v h="$h" '
      /^[[:space:]]*\.[A-Za-z0-9_]+ = \.\{/ { k = $1; u = "" }
      /\.url = "/ { u = $0; sub(/^[^"]*"/, "", u); sub(/".*$/, "", u) }
      /\.hash = "/ { if (index($0, "\"" h "\"")) { sub(/^\./, "", k); printf "%s\t%s\n", k, u; exit } }' "$f")"
    if [ -n "$info" ]; then echo "$info"; return 0; fi
  done < <(find /build/ghostty -name build.zig.zon | sort)
  return 1
}

# Zig 0.16 unpacks fetched packages next to the project (zig-pkg/); earlier layouts used the global cache.
declare -A pkg_dir=() pkg_hash=() pkg_url=()
for root in /build/zig-global/p /build/ghostty/zig-pkg /build/zig-local/p; do
  for d in "$root"/*/; do
    [ -d "$d" ] || continue
    h="$(basename "$d")"
    if info="$(package_info "$h")"; then
      name="${info%%$'\t'*}"
      pkg_dir[$name]="${d%/}"
      pkg_hash[$name]="$h"
      pkg_url[$name]="${info#*$'\t'}"
    else
      fail "fetched package $h (in $root) is named by no build.zig.zon"
    fi
  done
done

declare -A comp_dir=() comp_ver=() comp_src=()
add_component() { # name dir version source
  comp_dir[$1]="$2"
  comp_ver[$1]="$3"
  comp_src[$1]="$4"
}
license_id() {
  case "$1" in
    ghostty) echo "MIT" ;;
    simdutf) echo "MIT OR Apache-2.0" ;;
    highway) echo "Apache-2.0 OR BSD-3-Clause" ;;
    wuffs) echo "MIT OR Apache-2.0" ;;
    uucode) echo "MIT (data: Unicode-3.0; utf-8 decoder: MIT)" ;;
    zig) echo "MIT (musl parts: MIT; LLVM-derived parts: Apache-2.0 WITH LLVM-exception)" ;;
    *) echo "UNKNOWN (read the licence files)" ;;
  esac
}

add_component ghostty /build/ghostty "$GHOSTTY_COMMIT" "$GHOSTTY_URL at $GHOSTTY_COMMIT"
add_component zig "" "$ZIG_VERSION" "$ZIG_URL"
# A fetched package is a bundled component only if its code is in the archive: a member of the archive carries its
# name (simdutf.o, wuffs-v0.4.o, libhighway_zcu.o), or the lib-vt Zig module imports it (uucode is compiled into the
# module's own object, so it has no member of its own). Packages that fail both tests are build-time tools, data or
# test fixtures; they carry no licence in the tarball.
package_evidence() { # name -> prints the evidence; returns 1 when there is none
  local name="$1"
  if grep -qi -- "${name//_/[-_]}" /build/member-names.txt; then echo "an archive member is named after it"; return 0; fi
  if grep -q "addImport(\"$name\"" /build/ghostty/src/build/GhosttyZig.zig; then echo "imported by the lib-vt Zig module"; return 0; fi
  return 1
}
for name in "${!pkg_dir[@]}"; do
  if evidence="$(package_evidence "$name")"; then
    note "fetched package $name is bundled: $evidence"
  else
    note "fetched package $name is not bundled: no archive member is named after it and the lib-vt module does not import it; no licence collected"
    continue
  fi
  ver="$(sed -n 's/^[[:space:]]*\.version = "\(.*\)",$/\1/;T;p;q' "${pkg_dir[$name]}/build.zig.zon" 2>/dev/null || true)"
  # The version a package names for itself, else the commit in its url, else its content hash.
  if [ -z "$ver" ]; then
    if [[ "${pkg_url[$name]:-}" =~ ([0-9a-f]{40})\.t ]]; then ver="${BASH_REMATCH[1]}"; else ver="${pkg_hash[$name]}"; fi
  fi
  add_component "$name" "${pkg_dir[$name]}" "$ver" "${pkg_url[$name]:-package $name}"
done

# simdutf is a single source file in the ghostty tree: present when the archive holds its symbols.
simdutf_version="(not in the archive)"
if [ "$n_simdutf" -gt 0 ]; then
  simdutf_version="$(sed -n 's/^#define SIMDUTF_VERSION "\(.*\)"$/\1/;T;p;q' /build/ghostty/pkg/simdutf/vendor/simdutf.h)"
  if [ "$simdutf_version" != "$SIMDUTF_VERSION_PINNED" ]; then
    fail "simdutf in the ghostty tree is ${simdutf_version:-unknown}; the pinned licence files are for $SIMDUTF_VERSION_PINNED"
  fi
  add_component simdutf "" "$simdutf_version" "pkg/simdutf/vendor in the ghostty tree; licences from github.com/simdutf/simdutf at $SIMDUTF_COMMIT"
fi

# The archive must not hold code from a package that the graph did not fetch.
if [ "$n_hwy" -gt 0 ] && [ -z "${pkg_dir[highway]:-}" ]; then fail "the archive holds highway symbols but no highway package was fetched"; fi
if [ "$n_wuffs" -gt 0 ] && [ -z "${pkg_dir[wuffs]:-}" ]; then fail "the archive holds wuffs symbols but no wuffs package was fetched"; fi

# ---- Licences, for exactly those components. ----
lic="$stage/licenses"
mkdir -p "$lic"
copy_license_files() { # component dir
  local component="$1" dir="$2" n=0 f
  mkdir -p "$lic/$component"
  while IFS= read -r f; do
    cp "$f" "$lic/$component/$(basename "$f")"
    n=$((n + 1))
  done < <(find "$dir" -maxdepth 1 -type f \( -iname 'LICENSE*' -o -iname 'LICENCE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' -o -iname 'PATENTS*' \) | sort)
  if [ "$n" -gt 0 ]; then note "$n licence file(s) for $component in $dir"; else note "no licence file for $component in $dir"; fi
  copied_n="$n"
}

mapfile -t comp_names < <(printf '%s\n' "${!comp_dir[@]}" | sort)
for name in "${comp_names[@]}"; do
  [ -n "${comp_dir[$name]}" ] || continue
  copy_license_files "$name" "${comp_dir[$name]}"
  has_pinned=0
  for row in "${PINNED_LICENSE_FILES[@]}"; do
    if [ "${row%%|*}" = "$name" ]; then has_pinned=1; fi
  done
  if [ "$copied_n" = 0 ] && [ "$has_pinned" = 0 ]; then fail "no licence file for $name in ${comp_dir[$name]}"; fi
done

for row in "${PINNED_LICENSE_FILES[@]}"; do
  IFS='|' read -r component lname url sha <<< "$row"
  [ -n "${comp_dir[$component]+set}" ] || continue
  mkdir -p "$lic/$component"
  if curl -fsSL --retry 3 -o "$lic/$component/$lname" "$url" &&
    echo "${sha}  $lic/$component/$lname" | sha256sum -c -; then
    note "pinned licence file $component/$lname"
  else
    fail "pinned licence file $component/$lname could not be fetched or its sha256 differs"
  fi
done

# Zig's runtime support code is compiled into the archive (bundle_compiler_rt, bundle_ubsan_rt) with the
# parts of the standard library it uses. The files are in the Zig release itself.
mkdir -p "$lic/zig"
cp "$ZIG_DIR/LICENSE" "$lic/zig/LICENSE"
cp "$ZIG_DIR/lib/libc/musl/COPYRIGHT" "$lic/zig/musl-COPYRIGHT"
cp "$ZIG_DIR/lib/libunwind/LICENSE.TXT" "$lic/zig/llvm-LICENSE.TXT"
cat > "$lic/zig/NOTICE" <<EOF
Zig ${ZIG_VERSION} runtime support compiled into libghostty-vt.a (compiler_rt, and the standard library code
compiled into the library's own object). The ubsan runtime is requested by the build but is not in the archive
at ReleaseFast.

LICENSE            Zig itself, MIT (Expat).
musl-COPYRIGHT     musl's licence file as shipped in the Zig release. Parts of compiler_rt (floating-point
                   math such as sin, cos, exp, log, fma, fmod, round, trunc, sqrt, rem_pio2) are ports of
                   musl, which their source headers state is MIT licensed.
llvm-LICENSE.TXT   The LLVM Project licence file as shipped in the Zig release. Parts of compiler_rt
                   follow LLVM's compiler-rt (Apache-2.0 with LLVM Exceptions; releases up to 8.0
                   were MIT / UIUC), which their source headers name.

Source: ${ZIG_URL} (sha256 ${ZIG_SHA256}).
EOF

# licenses/INDEX: component, version, licence id, source. Sorted, so two runs agree.
for name in "${comp_names[@]}"; do
  printf '%s\t%s\t%s\t%s\n' "$name" "${comp_ver[$name]}" "$(license_id "$name")" "${comp_src[$name]}"
done | sort > "$lic/INDEX"

# Windows runtime support comes from Zig's bundled MinGW sources as well.
if [ "$TARGET" = x86_64-windows-gnu ]; then
  cp "$ZIG_DIR/lib/libc/mingw/COPYING" "$lic/zig/mingw-COPYING"
fi
find "$stage" -type d -exec chmod 755 {} +
find "$stage" -type f -exec chmod 644 {} +
name="libghostty-vt-${GHOSTTY_COMMIT}-${TARGET}.tar"
(cd "$stage" && tar --sort=name --format=gnu --numeric-owner --owner=0 --group=0 --mtime=@0 -cf "/out/$name" include lib licenses)
(cd /out && sha256sum "$name" > "$name.sha256")
(cd "$stage" && find . -type f -print0 | sort -z | xargs -0 sha256sum)
