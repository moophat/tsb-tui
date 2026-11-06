#!/usr/bin/env bash
set -euo pipefail

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
printf '\ntsb-tui: %s\nNext: tsb-tui --broker https://<host>\n' "$tui_binary"
case ":$PATH:" in
  *":$pipx_bin:"*) ;;
  *) printf '%s is not on PATH; run pipx ensurepath yourself to add it.\n' "$pipx_bin" ;;
esac
