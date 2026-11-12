#!/usr/bin/env bash
# leak-check.sh — the one definition of "environment detail that must not be in this repo".
#
# Two callers, one pattern:
#   .pre-commit-config.yaml   → leak-check.sh --staged --warn   (local, WARNING only, never blocks)
#   .github/workflows/*.yml   → leak-check.sh                   (CI, fails the PR)
#   the orchestrator's push:  → leak-check.sh --push <url> <branch> && git push <url> HEAD:refs/heads/<branch>
#
# Why warn locally and fail in CI: a blocking local hook gets bypassed with --no-verify the first
# time it is inconvenient; a warning that names the line is read, and the PR still cannot merge.
# Why one script: the earlier pattern lived only in the workflow, so the hook did not exist and
# reviewers found leaks by hand (2026-09-18).
#
# What every mode checks: PATTERN below defines the dev-subnet and CGNAT addresses, dev
# domain, secret paths, checkout and vault paths, and shared-mount name checked in this repo.
# Only --push also checks WORKSTATION_PATTERN: lane paths, the toolkit checkout, agent scratch
# paths, and workstation file URLs. What it cannot catch: node shorthand like ".14 to .10"
# — any pattern loose enough for that also matches version numbers and section references.
# Shorthand is prevented by the ops/ split and by review, not by this script.
set -uo pipefail

PATTERN='10\.98\.10\.[0-9]|100\.(6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\.[0-9]+\.[0-9]+|([a-z0-9-]+\.)?moophat\.cc|/root/\.secrets/|/opt/(ObsidianVault|tsb-(program|core|web)|lanes|tsb-test-venv)|/tmp/opencode|/mnt/shared'

# A second pattern, applied to a push range only, never to the full-tree scan. Older tracked files
# already hold some of these strings, so adding them to $PATTERN would fail CI on lines no push
# touches; cleaning those files is a separate issue. The full-tree scan keeps $PATTERN alone.
WORKSTATION_PATTERN='/opt/lanes/|/opt/agentoolkit|/tmp/claude-|/root/\.claude|file://(localhost)?/(opt|root|tmp)/'

# Paths the scan skips, each with a reason. Keep this list short and every entry justified:
# an exclusion is a place to park a leak.
EXCLUDES=(
  ':!.github/scripts/leak-check.sh'   # declares the pattern, so it matches itself
)

usage="usage: leak-check.sh [--staged] [--warn] | leak-check.sh --push <remote-url> <branch>"
warn=0; staged=0; push=0; remote=""; branch=""
while [ $# -gt 0 ]; do
  case "$1" in
    --warn) warn=1 ;;
    --staged) staged=1 ;;
    --push)
      if [ $# -lt 3 ]; then echo "$usage" >&2; exit 2; fi
      push=1; remote="$2"; branch="$3"; shift 2
      ;;
    *) echo "$usage" >&2; exit 2 ;;
  esac
  shift
done

# --warn is a local-hook softening that means nothing for a push range: refuse the pair.
if [ "$push" = 1 ] && [ "$warn" = 1 ]; then
  echo "::error::--warn does not apply to --push" >&2
  exit 2
fi

if [ "$push" = 1 ]; then
  # The base: the branch's tip on the remote, falling back to main when it is absent there. A
  # failed ls-remote or two empty answers means the range cannot be resolved — fail closed, never
  # a silent pass (the bug this mode exists to close).
  out="$(git ls-remote "$remote" "refs/heads/$branch" 2>/dev/null)" \
    || { echo "::error::git ls-remote the remote refs/heads/$branch failed — the scan did not run" >&2; exit 2; }
  base="$(printf '%s\n' "$out" | head -1 | cut -f1)"
  if [ -z "$base" ]; then
    out="$(git ls-remote "$remote" refs/heads/main 2>/dev/null)" \
      || { echo "::error::git ls-remote the remote refs/heads/main failed — the scan did not run" >&2; exit 2; }
    base="$(printf '%s\n' "$out" | head -1 | cut -f1)"
  fi
  if [ -z "$base" ]; then
    echo "::error::neither refs/heads/$branch nor refs/heads/main exists at the remote — the scan did not run" >&2
    exit 2
  fi
  # The base object must be in this repo to diff against; fetch it when it is not.
  if ! git cat-file -e "$base" 2>/dev/null; then
    git fetch -q "$remote" "$base" 2>/dev/null \
      || { echo "::error::git fetch of $base from the remote failed — the scan did not run" >&2; exit 2; }
  fi
  tmp=""; tmp2=""; tmp3=""; scan_tmp=""
  trap 'rm -f "$tmp" "$tmp2" "$tmp3" "$scan_tmp"' EXIT
  tmp="$(mktemp)" && [ -n "$tmp" ] \
    || { echo "::error::temporary diff allocation failed — the scan did not run" >&2; exit 2; }
  tmp2="$(mktemp)" && [ -n "$tmp2" ] \
    || { echo "::error::temporary record allocation failed — the scan did not run" >&2; exit 2; }
  tmp3="$(mktemp)" && [ -n "$tmp3" ] \
    || { echo "::error::temporary blob allocation failed — the scan did not run" >&2; exit 2; }
  scan_tmp="$(mktemp)" && [ -n "$scan_tmp" ] \
    || { echo "::error::added-line/file-name temporary allocation failed — the scan did not run" >&2; exit 2; }
  # The range must contain commits; HEAD equal to the base pushes nothing. Check rev-list itself so
  # a failed range is exit 2, not a false "nothing to push".
  commits="$(git rev-list "$base..HEAD" 2>/dev/null)"; rev_status=$?
  if [ "$rev_status" -ne 0 ]; then
    echo "::error::git rev-list $base..HEAD failed — the scan did not run" >&2
    rm -f "$tmp" "$tmp2" "$tmp3"; exit 2
  fi
  if [ -z "$commits" ]; then
    echo "nothing to push against $base" >&2
    rm -f "$tmp" "$tmp2" "$tmp3"
    exit 2
  fi

  # Merge commits fail closed: a merge's combined diff cannot be scanned truthfully line by line,
  # and the orchestrator pushes rebased branches. If the range holds any merge, name it and exit 2.
  git rev-list --merges "$base..HEAD" > "$tmp2" \
    || { echo "::error::git rev-list --merges $base..HEAD failed — the scan did not run" >&2; rm -f "$tmp" "$tmp2" "$tmp3"; exit 2; }
  if [ -s "$tmp2" ]; then
    IFS= read -r mc < "$tmp2"
    mshort="$(git rev-parse --short "$mc")" \
      || { echo "::error::git rev-parse --short for $mc failed — the scan did not run" >&2; rm -f "$tmp" "$tmp2" "$tmp3"; exit 2; }
    echo "::error::the push range holds a merge commit ($mshort); rebase it, or check it by hand" >&2
    rm -f "$tmp" "$tmp2" "$tmp3"
    exit 2
  fi

  # Gap 1: scan each commit in the range by its own diff, so a path one commit adds and a later
  # commit removes is still caught — not just the end state. Gaps 2-4: every added line, each binary
  # file a commit adds or changes, and the names of files added, renamed or copied. A hit names the
  # commit (short sha), the file and the line, then exits 1; a failure to compute exits 2.
  hits=""; n=0
  while IFS= read -r c; do
    short="$(git rev-parse --short "$c")" \
      || { echo "::error::git rev-parse --short for $c failed — the scan did not run" >&2; rm -f "$tmp" "$tmp2" "$tmp3"; exit 2; }

    # Gaps 1,2: added lines in this commit's own diff. A '+++ ' line is a file header only inside
    # the file's header block — after its 'diff --git' line and before that file's first '@@'. Inside
    # a hunk every '+' line is content and is scanned, so added content that itself starts with '+'
    # (shown as '++...', even '+++ <path>') counts. Track the file from the header's '+++ b/<path>'.
    # git show (not diff-tree) so a root commit — HEAD amended onto no parent — shows its additions.
    # --text forces a textual diff, --no-textconv/--no-ext-diff disable external converters and diff
    # drivers, so a file marked -diff (or binary) in .gitattributes still shows its added lines to
    # the line scan; the blob scan by object id stays as it is for real binaries.
    git show -p -r --no-color --format='' --text --no-textconv --no-ext-diff "$c" -- . "${EXCLUDES[@]}" > "$tmp" \
      || { echo "::error::git show for $short failed — the scan did not run" >&2; rm -f "$tmp"; exit 2; }
    curfile=""; in_header=0
    while IFS= read -r line; do
      case "$line" in
        'diff --git '*) in_header=1; curfile="" ;;
        '@@ '*)         in_header=0 ;;
      esac
      if [ "$in_header" = 1 ]; then
        # In the header block: the '+++ b/<path>' line names the file; it is not scanned.
        case "$line" in '+++ '*) p="${line#+++ }"; curfile="${p#b/}" ;; esac
        continue
      fi
      case "$line" in
        +*)
          if [ -n "$curfile" ]; then n=$((n+1)); fi
          printf '%s\n' "$line" > "$scan_tmp" \
            || { echo "::error::added-line temporary write failed — the scan did not run" >&2; exit 2; }
          grep -qiE "$PATTERN|$WORKSTATION_PATTERN" "$scan_tmp"; ag=$?
          if [ "$ag" -eq 0 ]; then
            hits="${hits}${short} ${curfile}: ${line}"$'\n'
          elif [ "$ag" -gt 1 ]; then
            echo "::error::added-line grep failed — the scan did not run" >&2; rm -f "$tmp" "$tmp2" "$tmp3"; exit 2
          fi
          ;;
      esac
    done < "$tmp"

    # Gaps 3,4,5: the files this commit adds or changes, read from NUL-separated raw records
    # (git diff-tree -r -z --raw -M --root). Because records are NUL-separated, no path is decoded
    # or unquoted: a tab, newline, quote or backslash in a name changes nothing. Each record is
    # ':<oldmode> <newmode> <oldsha> <newsha> <status>' then the path(s); R/C records carry source
    # then destination. Skip deletions (status D, new id all zeros). Read a binary's content by its
    # new blob id, never by a displayed path; the destination path is the one the name scan reads.
    git diff-tree -r -z --raw -M --root "$c" -- . "${EXCLUDES[@]}" > "$tmp2" \
      || { echo "::error::git diff-tree for $short failed — the scan did not run" >&2; rm -f "$tmp" "$tmp2" "$tmp3"; exit 2; }
    {
      IFS= read -r -d '' _sha   # first token is the commit id; skip it
      while IFS= read -r -d '' rec; do
        IFS=' ' read -r om nm oldsha newsha status <<< "$rec"
        st="${status:0:1}"
        IFS= read -r -d '' src; dest="$src"
        case "$st" in R|C) IFS= read -r -d '' dest ;; esac
        [ "$st" = "D" ] && continue   # deletion: new id all zeros, nothing to read
        # A raw record whose new mode is 160000 is a gitlink (a submodule pin): the commit it names
        # lives in another repository, so it is not a blob readable here. Skip the blob scan; the
        # name scan below still reads its path. A clean pin move (status M, not A/R/C) exits 0.
        if [ "$nm" != "160000" ]; then
          git cat-file -p "$newsha" > "$tmp3" \
            || { echo "::error::git cat-file for $short:$dest failed — the scan did not run" >&2; rm -f "$tmp" "$tmp2" "$tmp3"; exit 2; }
          # A NUL byte in the blob means git treats the file as binary. Grep the blob with -m1, then
          # strip NULs from the match so command substitution does not drop them; no head, so no
          # broken-pipe status, and g is the pipeline's own status (grep's exit, tr always exits 0).
          if [ "$(tr -d '\0' < "$tmp3" | wc -c)" != "$(wc -c < "$tmp3")" ]; then
            m="$(grep -a -m1 -iE "$PATTERN|$WORKSTATION_PATTERN" "$tmp3" | tr -d '\0')"
            g=$?
            if [ "$g" -gt 1 ]; then
              echo "::error::grep on $short:$dest failed — the scan did not run" >&2; rm -f "$tmp" "$tmp2" "$tmp3"; exit 2
            fi
            if [ -n "$m" ]; then hits="${hits}${short} ${dest}: ${m}"$'\n'; fi
          fi
        fi
        # Name scan: added, renamed or copied files, the destination name.
        case "$st" in A|R|C)
          printf '%s\n' "$dest" > "$scan_tmp" \
            || { echo "::error::file-name temporary write failed — the scan did not run" >&2; exit 2; }
          grep -qiE "$PATTERN|$WORKSTATION_PATTERN" "$scan_tmp"; ng=$?
          if [ "$ng" -eq 0 ]; then
            hits="${hits}${short} ${dest}: (file name)"$'\n'
          elif [ "$ng" -gt 1 ]; then
            echo "::error::file-name grep failed — the scan did not run" >&2; rm -f "$tmp" "$tmp2" "$tmp3"; exit 2
          fi
          ;;
        esac
      done
    } < "$tmp2"
  done <<< "$commits"
  rm -f "$tmp" "$tmp2" "$tmp3"

  if [ -n "$hits" ]; then
    echo "::error::environment detail in lines this push would add — it belongs in tsb-program/ops/environment.md"
    printf '%s\n' "${hits%$'\n'}" | sed 's/^/  /' >&2
    exit 1
  fi
  echo "clean against $(git rev-parse --short "$base"): $n added lines"
  exit 0
fi

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
