#!/usr/bin/env bash
# Verify every vendored header and the shared object, and bind the object hash to PIN.
set -euo pipefail
cd "$(dirname "$0")/../vendor/libghostty-vt"
sha256sum --check --strict SHA256SUMS
"${PYTHON:-python3}" - <<'PY'
import re
from pathlib import Path

pin = Path("PIN").read_text()
values = re.findall(r"^so_sha256:\s*([0-9a-f]{64})\s*$", pin, re.MULTILINE)
if len(values) != 1:
    raise SystemExit("engine-check: PIN must contain exactly one so_sha256")
entries = {}
for line in Path("SHA256SUMS").read_text().splitlines():
    match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
    if not match or match[2] in entries:
        raise SystemExit("engine-check: malformed or duplicate checksum entry")
    entries[match[2]] = match[1]
headers = {p.as_posix() for p in Path("include").rglob("*.h") if p.is_file()}
if not headers or set(entries) != headers | {"libghostty-vt.so"}:
    raise SystemExit("engine-check: SHA256SUMS must list the shared object and every header exactly once")
if entries["libghostty-vt.so"] != values[0]:
    raise SystemExit("engine-check: shared object checksum disagrees with PIN so_sha256")
print("engine-check: shared object and all headers verified; object matches PIN")
PY
