#!/usr/bin/env bash
set -euo pipefail
umask 022
exec python3 "$(dirname "$0")/assemble.py" "$@"
