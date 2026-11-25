#!/usr/bin/env bash
# contract-pin.sh — populate and verify program/, the one sparse, partial submodule of tsb-program.
#
#   contract-pin.sh init    make contract-init   (idempotent; never `git submodule update`, never `deinit`)
#   contract-pin.sh check   make contract-check  (fail-closed prerequisite of test and fixtures)
#
# program/ holds srs/ and contract/ of tsb-program at the commit this branch pins, and nothing else.
# The sparse and filter settings live in the submodule's own gitdir, per clone, so .gitmodules cannot
# carry them: `update = none` keeps every automatic path (clone --recurse-submodules, submodule update
# --init, submodule.recurse) from populating a full checkout, and `init` below is the only thing that
# does. The mechanics and their rationale: tsb-core `harness/contract-pin.sh`, this script's source.
#
# Environment:
#   CONTRACT_CI_RESET=1     init: reset and clean an existing program/ first (CI reuses a workspace)
#   CONTRACT_MAIN_REF=REF   check: also require the pinned commit to be an ancestor of REF in program/
set -euo pipefail

mode="${1:-}"
case "$mode" in init | check | copy | copy-check) ;; *) echo "usage: $0 init|check|copy|copy-check" >&2; exit 2 ;; esac

cd "$(dirname "$0")/.."

SUB=program
NAME=contract
WANT_URL='../tsb-program.git'
RELEASE_LINE='^v[0-9]+\.[0-9]+\.[0-9]+ tag tsb-release$'
# Where git keeps an absorbed submodule's gitdir. In a main checkout --git-dir is .git; in a
# linked worktree it is that worktree's own admin dir (.git/worktrees/<id>). `git submodule
# absorbgitdirs` targets exactly $MODULES/$NAME in both, so this is the directory to delete
# when a stale clone has to be cleaned up, and the repair line below is true in both.
MODULES="$(git rev-parse --git-dir)/modules"

fail() { echo "contract-$mode: $*" >&2; exit 1; }

# shape_fail MSG: a wrong clone is never repaired automatically; say how.
shape_fail() {
  echo "contract-$mode: $*" >&2
  echo "contract-$mode: repair: empty $SUB/, delete $MODULES/$NAME, rerun make contract-init" >&2
  exit 1
}

# cfg KEY: the value in program/'s own config, or empty.
cfg() { git -C "$SUB" config --get "$1" 2>/dev/null || true; }

gitmodules_ok() {
  local keys want v
  # Reject anything in .gitmodules other than this one submodule's three keys: a second
  # submodule section, an extra key in this one (a branch, a second path), or a missing key
  # would otherwise let a recursive checkout populate something this gate never inspects.
  # --name-only lists a key once even when duplicated (last value wins), so a duplicate whose
  # final value is still correct is harmless and one whose value differs fails below.
  keys="$(git config -f .gitmodules --name-only --list 2>/dev/null | LC_ALL=C sort || true)"
  want="$(printf '%s\n' "submodule.$NAME.path" "submodule.$NAME.url" "submodule.$NAME.update" | LC_ALL=C sort)"
  [ "$keys" = "$want" ] ||
    fail ".gitmodules must declare only submodule.$NAME with path, url and update; found: $(git config -f .gitmodules --name-only --list 2>/dev/null | LC_ALL=C sort | tr '\n' ' ')"
  v="$(git config -f .gitmodules --get "submodule.$NAME.path" 2>/dev/null || true)"
  [ "$v" = "$SUB" ] || fail ".gitmodules: submodule.$NAME.path is '$v', must be '$SUB'"
  v="$(git config -f .gitmodules --get "submodule.$NAME.url" 2>/dev/null || true)"
  [ "$v" = "$WANT_URL" ] || fail ".gitmodules: submodule.$NAME.url is '$v', must be '$WANT_URL'"
  v="$(git config -f .gitmodules --get "submodule.$NAME.update" 2>/dev/null || true)"
  [ "$v" = none ] || fail ".gitmodules: submodule.$NAME.update is '$v', must be 'none' (else clone --recurse-submodules and submodule update populate a full checkout)"
}

# The shape of the clone: sparse (cone, exactly srs and contract), partial (blob:none).
shape_ok() {
  local want="${1:-fail}" v
  v="$(cfg core.sparseCheckout)$(cfg core.sparseCheckoutCone)"
  [ "$v" = truetrue ] || "$want" "$SUB/ is not a sparse cone checkout (core.sparseCheckout/core.sparseCheckoutCone are not both true)"
  v="$(git -C "$SUB" sparse-checkout list 2>/dev/null | LC_ALL=C sort | tr '\n' ' ')"
  [ "$v" = "contract srs " ] || "$want" "$SUB/ sparse-checkout list is '${v% }', must be exactly 'contract srs'"
  [ "$(cfg remote.origin.promisor)" = true ] || "$want" "$SUB/ is not a partial clone (remote.origin.promisor is not true)"
  [ "$(cfg remote.origin.partialclonefilter)" = blob:none ] || "$want" "$SUB/ is not a blob:none partial clone (remote.origin.partialclonefilter)"
}

if [ "$mode" = copy ]; then
  gitmodules_ok
  shape_ok
  tags="$(git -C "$SUB" for-each-ref --points-at HEAD --format='%(refname:short) %(objecttype) %(taggername)' 'refs/tags/v*')"
  grep -Eq "$RELEASE_LINE" <<<"$tags" ||
    fail "no release tag at the pinned commit $(git -C "$SUB" rev-parse --short=12 HEAD): need an annotated vX.Y.Z tag by tsb-release; found: ${tags:-none}"
  tag="$(grep -Eo '^v[0-9.]+' <<<"$tags" | head -n1)"
  commit="$(git -C "$SUB" rev-parse HEAD)"
  tree="$(git -C "$SUB" rev-parse HEAD:contract)"

  rm -rf contract
  git -C "$SUB" archive --prefix=contract/ HEAD:contract | tar -x

  cat > contract.pin <<EOF
tag: $tag
commit: $commit
tree: $tree
EOF

  echo "contract-copy: copied contract/ at $tag ($tree)"
  exit 0
fi

if [ "$mode" = copy-check ]; then
  [ -f contract.pin ] || fail "contract.pin is missing"
  [ -d contract ] || fail "contract/ is missing"
  [ -f contract/CONTRACT_VERSION ] || fail "contract/CONTRACT_VERSION is missing"

  pin_tag="$(grep -E '^tag:[[:space:]]+' contract.pin | awk '{print $2}')"
  pin_commit="$(grep -E '^commit:[[:space:]]+' contract.pin | awk '{print $2}')"
  pin_tree="$(grep -E '^tree:[[:space:]]+' contract.pin | awk '{print $2}')"
  [ -n "$pin_tag" ] || fail "contract.pin: missing tag"
  [ -n "$pin_commit" ] || fail "contract.pin: missing commit"
  [ -n "$pin_tree" ] || fail "contract.pin: missing tree"

  version="$(tr -d '[:space:]' < contract/CONTRACT_VERSION)"
  [ "$version" = "$pin_tag" ] ||
    fail "contract/CONTRACT_VERSION ($version) does not match contract.pin tag ($pin_tag)"

  git diff --quiet -- contract contract.pin ||
    fail "working tree differs from index for contract/ or contract.pin"
  untracked="$(git ls-files --others --exclude-standard -- contract contract.pin)"
  [ -z "$untracked" ] ||
    fail "untracked files in contract/ or contract.pin: $untracked"

  index_tree="$(git write-tree --prefix=contract 2>/dev/null || true)"
  [ -n "$index_tree" ] || fail "contract/ is not in the index"
  [ "$index_tree" = "$pin_tree" ] ||
    fail "index contract tree ($index_tree) does not match contract.pin tree ($pin_tree)"

  echo "contract-copy-check: contract/ matches contract.pin at $pin_tag ($pin_tree)"
  exit 0
fi

if [ "$mode" = check ]; then
  gitmodules_ok

  # The staged gitlink must exist; status then says whether program/ is at it.
  st="$(git submodule status -- "$SUB")"
  [ -n "$st" ] || fail "no gitlink at $SUB in the index"
  case "${st:0:1}" in
    ' ') ;;
    -) fail "$SUB/ is not populated: run make contract-init" ;;
    +) fail "$SUB/ is at a different commit than this branch pins: run make contract-init (status: $st)" ;;
    U) fail "$SUB/ is conflicted (status: $st)" ;;
    *) fail "unexpected submodule status: $st" ;;
  esac

  shape_ok
  # The effective local update policy must be none, as init requires: an override defeats
  # update=none and lets `git submodule update` populate a full checkout.
  upd="$(git config --get "submodule.$NAME.update" 2>/dev/null || true)"
  [ "$upd" = none ] ||
    fail "submodule.$NAME.update in local config is '$upd', must be 'none' (a local override defeats update=none and lets a submodule update populate a full checkout); run make contract-init (it sets it from .gitmodules) or unset the override (git config --local --unset submodule.$NAME.update)"
  # Cone mode brings root files, never another directory; a full checkout shows up here.
  extra="$(find "$SUB" -mindepth 1 -maxdepth 1 -type d ! -name .git ! -name srs ! -name contract -printf '%f ')"
  [ -z "$extra" ] || fail "$SUB/ holds directories other than srs/ and contract/: $extra"

  dirty="$(git -C "$SUB" status --porcelain --untracked-files=all)"
  [ -z "$dirty" ] || fail "$SUB/ is not clean (the pinned tree is read-only):"$'\n'"$dirty"

  if [ -n "${CONTRACT_MAIN_REF:-}" ]; then
    git -C "$SUB" merge-base --is-ancestor HEAD "$CONTRACT_MAIN_REF" ||
      fail "pinned commit $(git -C "$SUB" rev-parse --short=12 HEAD) is not an ancestor of $CONTRACT_MAIN_REF (an unmerged tsb-program commit)"
  fi

  tags="$(git -C "$SUB" for-each-ref --points-at HEAD --format='%(refname:short) %(objecttype) %(taggername)' 'refs/tags/v*')"
  grep -Eq "$RELEASE_LINE" <<<"$tags" ||
    fail "no release tag at the pinned commit $(git -C "$SUB" rev-parse --short=12 HEAD): need an annotated vX.Y.Z tag by tsb-release (history tags contract-v*, lightweight and hand-made tags never count); found: ${tags:-none}"

  [ "$(git config --get diff.submodule 2>/dev/null || true)" != diff ] ||
    fail "diff.submodule=diff makes git diff run inside $SUB/; unset it (git config --unset diff.submodule)"

  [ -f contract.pin ] || fail "contract.pin is missing"
  [ -d contract ] || fail "contract/ is missing"
  [ -f contract/CONTRACT_VERSION ] || fail "contract/CONTRACT_VERSION is missing"

  pin_tag="$(grep -E '^tag:[[:space:]]+' contract.pin | awk '{print $2}')"
  pin_commit="$(grep -E '^commit:[[:space:]]+' contract.pin | awk '{print $2}')"
  pin_tree="$(grep -E '^tree:[[:space:]]+' contract.pin | awk '{print $2}')"
  [ -n "$pin_tag" ] || fail "contract.pin: missing tag"
  [ -n "$pin_commit" ] || fail "contract.pin: missing commit"
  [ -n "$pin_tree" ] || fail "contract.pin: missing tree"

  gitlink_sha="$(git rev-parse ":$SUB")"
  [ "$pin_commit" = "$gitlink_sha" ] ||
    fail "contract.pin commit ($pin_commit) does not match gitlink ($gitlink_sha)"

  prog_tag="$(grep -Eo '^v[0-9.]+' <<<"$tags" | head -n1)"
  [ "$pin_tag" = "$prog_tag" ] ||
    fail "contract.pin tag ($pin_tag) does not match release tag ($prog_tag)"

  program_tree="$(git -C "$SUB" rev-parse HEAD:contract)"
  [ "$pin_tree" = "$program_tree" ] ||
    fail "contract.pin tree ($pin_tree) does not match program/contract ($program_tree)"

  git diff --quiet -- contract contract.pin ||
    fail "working tree differs from index for contract/ or contract.pin"
  untracked="$(git ls-files --others --exclude-standard -- contract contract.pin)"
  [ -z "$untracked" ] ||
    fail "untracked files in contract/ or contract.pin: $untracked"

  index_tree="$(git write-tree --prefix=contract 2>/dev/null || true)"
  [ -n "$index_tree" ] || fail "contract/ is not in the index"
  [ "$index_tree" = "$program_tree" ] ||
    fail "index contract tree ($index_tree) does not match program/contract ($program_tree)"

  if [ "${CONTRACT_COMMITTED:-0}" = 1 ]; then
    git diff --quiet HEAD -- contract contract.pin ||
      fail "committed state differs from index for contract/ or contract.pin (CONTRACT_COMMITTED=1)"
  fi

  echo "contract-check: $SUB/ and contract/ clean at $(git -C "$SUB" rev-parse --short=12 HEAD), $prog_tag"
  exit 0
fi

# ---- init ------------------------------------------------------------------------------------------

if [ "${CONTRACT_CI_RESET:-}" = 1 ] && [ -e "$SUB/.git" ]; then
  git -C "$SUB" reset --hard
  git -C "$SUB" clean -ffdx
fi

gitmodules_ok
git submodule init "$SUB"

# git submodule init copies the url from .gitmodules into local config only when it is not
# already set, so a pre-existing submodule.$NAME.url override survives it and would be the
# clone source. `git submodule sync` recomputes that url from .gitmodules resolved against the
# superproject's default remote (the same resolution a submodule clone uses), so comparing
# before and after refuses a local override instead of fetching from it; the resolved value is
# then the clone source. The update policy is validated the same way: a local override there
# defeats update=none and lets `submodule update` populate a full checkout.
url_seen="$(git config --get "submodule.$NAME.url" 2>/dev/null || true)"
if ! sync_out="$(git submodule sync "$SUB" 2>&1)"; then
  fail "git submodule sync $SUB failed: $sync_out"
fi
url="$(git config --get "submodule.$NAME.url" 2>/dev/null || true)"
if [ -n "$url_seen" ] && [ "$url_seen" != "$url" ]; then
  fail "submodule.$NAME.url in local config is '$url_seen', not '$url' ($WANT_URL resolved against the default remote); unset it (git config --local --unset submodule.$NAME.url) and rerun"
fi
upd="$(git config --get "submodule.$NAME.update" 2>/dev/null || true)"
[ "$upd" = none ] ||
  fail "submodule.$NAME.update in local config is '$upd', must be 'none' (a local override defeats update=none and lets a submodule update populate a full checkout); unset it (git config --local --unset submodule.$NAME.update) and rerun"

link="$(git ls-files -s -- "$SUB")"
[ "${link:0:6}" = 160000 ] || fail "no gitlink at $SUB in the index"
sha="$(git rev-parse ":$SUB")"

if [ ! -e "$SUB/.git" ]; then
  if [ -d "$SUB" ] && [ -n "$(ls -A "$SUB")" ]; then
    fail "$SUB/ is not empty and is not a clone"$'\n'"repair: empty $SUB/, delete $MODULES/$NAME, rerun make contract-init"
  fi
  # A clone whose .git was removed leaves the absorbed gitdir behind; absorbing the new
  # clone's .git onto that non-empty directory aborts, so fail here with the repair line
  # instead of after a half-run. deinit is forbidden, so this is never repaired automatically.
  if [ -e "$MODULES/$NAME" ]; then
    shape_fail "stale submodule gitdir $MODULES/$NAME remains though $SUB/.git is absent; absorbing the new clone onto it would abort"
  fi
  git clone --filter=blob:none --no-checkout --sparse "$url" "$SUB"
  git -C "$SUB" sparse-checkout set --cone srs contract
  git -C "$SUB" checkout --detach "$sha"
  git submodule absorbgitdirs "$SUB"
else
  # Present: it must already be the clone `check` describes; a wrong one is repaired by hand, never here.
  shape_ok shape_fail
  if ! git -C "$SUB" cat-file -e "$sha^{commit}" 2>/dev/null ||
    [ -z "$(git -C "$SUB" for-each-ref --points-at "$sha" 'refs/tags/v*')" ]; then
    git -C "$SUB" fetch origin --tags
  fi
  git -C "$SUB" checkout --detach "$sha"
fi

echo "contract-init: $SUB/ at $(git -C "$SUB" rev-parse --short=12 HEAD)"
