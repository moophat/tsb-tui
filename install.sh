#!/usr/bin/env bash
set -euo pipefail

# Local release directories still work without a base or broker.
base="${TSB_TUI_BASE:-@@TSB_ORIGIN@@/downloads/tsb-tui}"
broker="${TSB_TUI_BROKER:-}"
version="${TSB_TUI_VERSION:-}"
while (( $# )); do
  case "$1" in
    --base|--broker)
      if (( $# < 2 )) || [[ -z "$2" ]]; then
        echo "$1 requires a URL." >&2; exit 1
      fi
      if [[ "$1" == --base ]]; then base="$2"; else broker="$2"; fi
      shift 2 ;;
    *) echo "Unknown argument: $1 (use --base <url> or --broker <url>)." >&2; exit 1 ;;
  esac
done
# These installer controls are not application configuration knobs.
unset TSB_TUI_BASE TSB_TUI_VERSION TSB_TUI_BROKER
if [[ "$(uname -s)" == Darwin ]]; then
  echo 'macOS has no release artifact yet; macOS support is pending.' >&2
  exit 1
fi

if [[ "$(uname -m)" != x86_64 ]]; then
  echo "tsb-tui's release ships the screen engine for x86_64 Linux only." >&2
  exit 1
fi

libc="$(getconf GNU_LIBC_VERSION 2>/dev/null || true)"
if [[ ! "$libc" =~ ^glibc\ ([0-9]+)\.([0-9]+)$ ]] ||
   (( 10#${BASH_REMATCH[1]} < 2 || (10#${BASH_REMATCH[1]} == 2 && 10#${BASH_REMATCH[2]} < 29) )); then
  echo 'tsb-tui requires glibc 2.29 or newer.' >&2
  exit 1
fi

# pipx runs the TUI on the interpreter we give it; the release needs 3.12 or newer.
py=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    py="$candidate"
    break
  fi
done
if [[ -z "$py" ]]; then
  echo "tsb-tui needs Python 3.12 or newer." >&2
  exit 1
fi

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    echo 'Fetching requires curl or wget.' >&2; return 1
  fi
}

shopt -s nullglob
tui_wheels=(tsb_tui-*.whl)
if (( ${#tui_wheels[@]} == 0 )); then
  if [[ "$base" == *'@@TSB_''ORIGIN@@'* ]]; then
    echo 'No download base: pass --base <url> or set TSB_TUI_BASE.' >&2
    exit 1
  fi
  base="${base%/}"
  origin="$("$py" - "$base" <<'PYTHON'
import sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[1])
if u.scheme not in ('http', 'https') or not u.netloc or u.username or u.password or u.query or u.fragment:
    raise SystemExit('Invalid download base URL.')
print(f'{u.scheme}://{u.netloc}')
PYTHON
)"
  broker="${broker:-$origin}"
  assets_dir="$(mktemp -d)"
  trap 'rm -rf -- "$assets_dir"' EXIT
  cd "$assets_dir"
  if [[ -n "$version" ]]; then
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
      echo 'TSB_TUI_VERSION must be x.y.z.' >&2; exit 1;
    }
    release_dir="v$version"
  else
    fetch "$base/latest.json" latest.json
    release_dir="$("$py" - <<'PYTHON'
import json
import re
from pathlib import Path
m = json.loads(Path('latest.json').read_text(encoding='utf-8'))
if type(m.get('schema')) is not int or m['schema'] != 1:
    raise SystemExit('latest.json must use schema 1.')
v = m.get('version', '')
if not isinstance(v, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', v):
    raise SystemExit('Invalid release version.')
if m.get('tag') != f'v{v}' or m.get('dir') != f'v{v}/':
    raise SystemExit('Invalid release tag or directory.')
print(m['tag'])
PYTHON
)"
  fi
  fetch "$base/$release_dir/SHA256SUMS" SHA256SUMS
  # Select safe file names and require manifest/checksum agreement before downloading assets.
  "$py" - <<'PYTHON' > selected-assets
import json
import re
from pathlib import Path
checks = {}
for line in Path('SHA256SUMS').read_text().splitlines():
    match = re.fullmatch(r'([0-9a-fA-F]{64}) [ *]([A-Za-z0-9._-]+)', line)
    if not match or match[2] in checks:
        raise SystemExit('Invalid or duplicate SHA256SUMS entry.')
    checks[match[2]] = match[1].lower()
selected = []
for pattern in (r'tsb_tui-[A-Za-z0-9._-]+\.whl', r'tsb_contract-[A-Za-z0-9._-]+\.whl',
                r'libghostty-vt-linux-x86_64\.tar\.gz', r'install\.sh'):
    names = [n for n in checks if re.fullmatch(pattern, n)]
    if len(names) != 1:
        raise SystemExit(f'Expected exactly one asset matching {pattern}.')
    selected.extend(names)
if Path('latest.json').exists():
    assets = json.loads(Path('latest.json').read_text())['assets']
    for name in selected:
        entry = assets[name]
        if entry.get('sha256', '').lower() != checks[name] or type(entry.get('size')) is not int or entry['size'] < 0:
            raise SystemExit(f'Manifest/checksum disagreement for {name}.')
print('\n'.join(selected))
PYTHON
  while IFS= read -r asset; do
    fetch "$base/$release_dir/$asset" "$asset"
  done < selected-assets
fi

shopt -s nullglob
tui_wheels=(tsb_tui-*.whl)
contract_wheels=(tsb_contract-*.whl)
if (( ${#tui_wheels[@]} != 1 || ${#contract_wheels[@]} != 1 )); then
  echo 'Expected exactly one tsb-tui wheel and one tsb-contract wheel in this directory.' >&2
  exit 1
fi
tui_wheel="${tui_wheels[0]}"
contract_wheel="${contract_wheels[0]}"
engine=libghostty-vt-linux-x86_64.tar.gz

# Check only this install's assets, including the installer itself, before invoking pipx.
for asset in "$tui_wheel" "$contract_wheel" "$engine" install.sh; do
  checksum="$(awk -v asset="$asset" '
    $2 == asset { print; count++ }
    END { if (count != 1) exit 1 }
  ' SHA256SUMS)" || {
    echo "Expected exactly one checksum for $asset in SHA256SUMS." >&2
    exit 1
  }
  printf '%s\n' "$checksum" | sha256sum --check --strict -
done

# pipx may fetch Python dependencies; all TSB pieces come from the checked local assets.
pipx install --force --python "$py" "$tui_wheel"
pipx inject --force tsb-tui "$contract_wheel"
venv="$(pipx environment --value PIPX_LOCAL_VENVS)/tsb-tui"
mkdir -p "$venv/lib/tsb-tui"
tar -xzf "$engine" -C "$venv/lib/tsb-tui" libghostty-vt.so

# The TUI loads the engine from this path; prove it loads before the first drive.
if ! "$venv/bin/python" -c 'import sys; from tsb_tui.term import ghostty; ghostty.load(sys.argv[1])' \
     "$venv/lib/tsb-tui/libghostty-vt.so"; then
  echo "The screen engine did not load; see the error above." >&2
  exit 1
fi

pipx_bin="$(pipx environment --value PIPX_BIN_DIR)"
tui_binary="$pipx_bin/tsb-tui"
"$tui_binary" drive --help
config="${XDG_CONFIG_HOME:-$HOME/.config}/tsb-tui/config.toml"
if [[ -e "$config" ]]; then
  printf 'Keeping existing config: %s\n' "$config"
elif [[ -z "$broker" ]]; then
  printf 'No broker known; no config written at %s. Pass --broker <url> to the installer.\n' "$config"
else
"$py" - "$config" "$broker" <<'PYTHON'
import json
import sys
from pathlib import Path
p = Path(sys.argv[1])
broker = sys.argv[2]
if p.exists():
    print(f'Keeping existing config: {p}')
elif not broker:
    print(f'No broker known; no config written at {p}. Pass --broker <url> to the installer.')
else:
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open('x', encoding='utf-8') as f:
            f.write('[broker]\nurl = ' + json.dumps(broker, ensure_ascii=False) + '\n')
    except FileExistsError:
        print(f'Keeping existing config: {p}')
    else:
        print(f'Config: {p}; broker: {broker}')
PYTHON
fi
printf '\ntsb-tui: %s\nType tsb-tui (or supply --broker <url> if no broker is configured).\n' "$tui_binary"
case ":$PATH:" in
  *":$pipx_bin:"*) ;;
  *) printf '%s is not on PATH; run pipx ensurepath yourself to add it.\n' "$pipx_bin" ;;
esac
