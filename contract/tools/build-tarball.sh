#!/usr/bin/env bash
# build-tarball.sh — the contract's release tarball, tsb-<CONTRACT_VERSION>.tar.gz.
#
#   bash contract/tools/build-tarball.sh [OUT_DIR]      (default: ./dist)
#
# Ships exactly: openapi/ schemas/ fixtures/ protocols/ tsbc/ client/ README.md CONTRACT_VERSION
# pyproject.toml, under one top-level directory named after the version. Nothing else: no tests,
# no tools, no caches, no dotfiles. Strangers get this file, and every published code repo also carries the same `contract/` tree.
#
# Before it packs anything it runs `tsbc verify --self-check`, which includes the credential-
# hygiene scan; any finding aborts the build. The output is reproducible: sorted entries, fixed
# owner and modes, and one mtime for every entry — SOURCE_DATE_EPOCH if set, else the last
# commit touching the tree, else 0 — with no timestamp or name in the gzip header.
#
# Refuses a -dev CONTRACT_VERSION (a release is a tag), and a tree with uncommitted changes
# under the shipped paths unless ALLOW_DIRTY=1, so what ships is what the tag records.
set -euo pipefail

tree="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${1:-$PWD/dist}"
version="$(tr -d '[:space:]' < "$tree/CONTRACT_VERSION")"
shipped=(openapi schemas fixtures protocols tsbc client README.md CONTRACT_VERSION pyproject.toml LICENSE)

if ! [[ "$version" =~ ^((contract-v[0-9]+\.[0-9]+(\.[0-9]+)?)|(v[0-9]+\.[0-9]+\.[0-9]+))$ ]]; then
  echo "error: CONTRACT_VERSION is '$version'; a release tarball is built only for contract-vX.Y, contract-vX.Y.Z or vX.Y.Z" >&2
  exit 1
fi

in_repo=0
if command -v git >/dev/null 2>&1 && git -C "$tree" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  in_repo=1
fi
if [ "$in_repo" = 1 ] && [ "${ALLOW_DIRTY:-}" != 1 ]; then
  dirty="$(git -C "$tree" status --porcelain --untracked-files=all -- "${shipped[@]}")"
  if [ -n "$dirty" ]; then
    echo "error: uncommitted changes under the shipped paths (ALLOW_DIRTY=1 to build anyway):" >&2
    printf '%s\n' "$dirty" | sed 's/^/  /' >&2
    exit 1
  fi
fi

echo "self-check before packing:"
if ! PYTHONPATH="$tree" python3 -m tsbc verify --self-check --contract-dir "$tree"; then
  echo "error: self-check failed; nothing was packed" >&2
  exit 1
fi

mtime="${SOURCE_DATE_EPOCH:-}"
if [ -z "$mtime" ] && [ "$in_repo" = 1 ]; then
  mtime="$(git -C "$tree" log -1 --format=%ct -- . 2>/dev/null || true)"
fi
mtime="${mtime:-0}"

mkdir -p "$out"
python3 - "$tree" "$out" "$version" "$mtime" "${shipped[@]}" <<'PY'
import gzip
import hashlib
import io
import sys
import tarfile
from pathlib import Path

tree, out, version, mtime = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
shipped = sys.argv[5:]
top = f"tsb-{version}"


def skipped(rel: Path) -> bool:
    return any(part.startswith(".") or part == "__pycache__" for part in rel.parts) or rel.suffix == ".pyc"


entries = []  # (archive name, path or None for a directory)
for name in shipped:
    path = tree / name
    if path.is_symlink():  # checked before anything reads through it: a link could ship host files
        sys.exit(f"error: {name} is a symlink; the tarball ships regular files only")
    if not path.exists():
        sys.exit(f"error: {name} is missing from the tree")
    if path.is_file():
        entries.append((name, path))
        continue
    entries.append((name, None))
    for sub in sorted(path.rglob("*")):
        rel = sub.relative_to(tree)
        if skipped(rel.relative_to(name)):
            continue
        if sub.is_symlink():
            sys.exit(f"error: {rel} is a symlink; the tarball ships regular files only")
        entries.append((rel.as_posix(), None if sub.is_dir() else sub))
entries.sort(key=lambda e: e[0])

raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
    def add(name, path):
        info = tarfile.TarInfo(f"{top}/{name}" if name else top)
        info.mtime, info.uid, info.gid, info.uname, info.gname = mtime, 0, 0, "", ""
        if path is None:
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            tar.addfile(info)
        else:
            data = path.read_bytes()
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    add("", None)
    for name, path in entries:
        add(name, path)

target = out / f"{top}.tar.gz"
with open(target, "wb") as fh, gzip.GzipFile(filename="", fileobj=fh, mode="wb", mtime=mtime, compresslevel=9) as gz:
    gz.write(raw.getvalue())
digest = hashlib.sha256(target.read_bytes()).hexdigest()
files = sum(1 for _, p in entries if p is not None)
print(f"built {target} ({files} files, sha256 {digest})")
PY
