#!/usr/bin/env bash
# leak-check.sh — the one definition of "environment detail that must not be in this repo".
#
# Two callers, one pattern:
#   .pre-commit-config.yaml   → leak-check.sh --staged --warn   (local, WARNING only, never blocks)
#   .github/workflows/*.yml   → leak-check.sh                   (CI, fails the PR)
#
# Why warn locally and fail in CI: a blocking local hook gets bypassed with --no-verify the first
# time it is inconvenient; a warning that names the line is read, and the PR still cannot merge.
# Why one script: the earlier pattern lived only in the workflow, so the hook did not exist and
# reviewers found leaks by hand (2026-09-18).
#
# What it catches: full node addresses on the dev subnet, the Tailscale CGNAT range, the dev
# domain (bare or with a label), literal secret paths, the dev workstation's checkout, vault, lane
# and test-venv paths, the agent scratch directory, and the swarm's shared-mount name. What it
# cannot catch: node shorthand like ".14 to .10"
# — any pattern loose enough for that also matches version numbers and section references.
# Shorthand is prevented by the ops/ split and by review, not by this script.
set -uo pipefail

PATTERN='10\.98\.10\.[0-9]|100\.(6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\.[0-9]+\.[0-9]+|([a-z0-9-]+\.)?moophat\.cc|/root/\.secrets/|/opt/(ObsidianVault|tsb-(program|core|web)|lanes|tsb-test-venv)|/tmp/opencode|/mnt/shared'

# Paths the scan skips, each with a reason. Keep this list short and every entry justified:
# an exclusion is a place to park a leak.
EXCLUDES=(
  ':!.github/scripts/leak-check.sh'   # declares the pattern, so it matches itself
)

warn=0; staged=0
for a in "$@"; do
  case "$a" in
    --warn) warn=1 ;;
    --staged) staged=1 ;;
    *) echo "usage: leak-check.sh [--staged] [--warn]" >&2; exit 2 ;;
  esac
done

if [ "$staged" = 1 ]; then
  # Only lines being added by the commit in progress. -U0 so context lines do not trip it.
  hits="$(git diff --cached -U0 -- . "${EXCLUDES[@]}" | grep -E '^\+[^+]' | grep -niE "$PATTERN" || true)"
else
  # Every tracked file. No -I: an address inside a tracked binary is still published.
  status=0
  hits="$(git grep --no-recurse-submodules -niE "$PATTERN" -- . "${EXCLUDES[@]}")" || status=$?
  # git grep: 0 = matches, 1 = none, >1 = the search itself failed. Treating every nonzero
  # exit as clean is how a broken guard reports success forever.
  if [ "$status" -gt 1 ]; then
    echo "::error::git grep exited $status — the scan did not run, so this is not a pass" >&2
    exit 1
  fi
fi

if [ -n "$hits" ]; then
  if [ "$warn" = 1 ]; then
    echo "WARNING: environment detail in staged changes — it belongs in tsb-program/ops/environment.md." >&2
    echo "         CI (the required test job, through make lint) will fail the PR on these lines:" >&2
  else
    echo "::error::environment detail found in tracked files — it belongs in tsb-program/ops/environment.md"
  fi
  printf '%s\n' "$hits" | sed 's/^/  /' >&2
  [ "$warn" = 1 ] && exit 0 || exit 1
fi

echo "clean: no node addresses, dev hostnames, secret paths, or workstation/mount paths"
exit 0
