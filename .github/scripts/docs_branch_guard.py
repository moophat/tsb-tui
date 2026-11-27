#!/usr/bin/env python3
"""docs_branch_guard.py — Qodo skips review by source-branch prefix
(`.pr_agent.toml`'s `ignore_pr_source_branches`), but nothing else proves a PR whose branch matches
one of those prefixes is actually docs-only. This is that proof: it fails closed if such a branch
touches anything but plans, ledgers, notes or Markdown — so a branch name alone can never let code
merge without review. A pin bump of the `program` submodule (the gitlink) is never docs, however
the diff names it.

Same pattern as `.github/scripts/leak-check.sh`: one script, called from CI with its context read
from `env:` (never interpolated into a shell command — `HEAD_REF` is a PR's own source branch
name, attacker-influenced on a fork). Called from the `test` job's own required check, on every
pull_request; a no-op on push (there is no PR branch to judge).

The prefix list is read from the BASE branch's own `.pr_agent.toml`
(`git show origin/<base>:.pr_agent.toml`) — the exact copy Qodo itself reads (Qodo reads it from
the default branch only, this repo's own `.pr_agent.toml` header), so this check and Qodo can never
disagree about which branches are exempted. A file confirmed absent from the base exempts no branches. Git errors and invalid
TOML fail closed. A file that parses fine but simply carries no `ignore_pr_source_branches` is not
a failure: nothing is exempted from Qodo review there either, so there is nothing this guard needs
to enforce — treating that the same as unparseable would fail every PR closed for as long as the
base branch predates the PR that first adds the key.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import tomllib

# A docs branch may touch plans and docs (any extension), or Markdown files elsewhere,
# including .aiworkflow/. Non-Markdown workflow files are code.
DOCS_ONLY_DIRS = ("plans/", "docs/")


def _run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, check=False)


def _prefixes_from_base(base_ref: str) -> tuple[bool, list[str]]:
    """`(ok, prefixes)`. A confirmed absent config returns `(True, [])`.
    `ok` is False when Git fails or the base config is not valid TOML at all — a real config problem this guard cannot see past, so the caller
    fails closed. A `.pr_agent.toml` that reads fine but simply has no (or a malformed)
    `ignore_pr_source_branches` is `(True, [])`, not a failure: Qodo itself then exempts no branch
    by name, so there is nothing this guard needs to enforce either — treating that the same as an
    unparseable file would fail every PR closed the moment `ignore_pr_source_branches` is not yet
    on the base branch (exactly the state of `main` before the PR that first adds it merges)."""
    proc = _run_git(["show", f"origin/{base_ref}:.pr_agent.toml"])
    if proc.returncode != 0:
        # ls-tree succeeds with empty output only when the tree exists and the path does not.
        # Unlike parsing git-show's diagnostic, this distinguishes absence from a Git failure.
        listing = _run_git(["ls-tree", "-z", f"origin/{base_ref}", "--", ".pr_agent.toml"])
        if listing.returncode == 0 and not listing.stdout:
            return True, []
        return False, []
    try:
        parsed = tomllib.loads(proc.stdout.decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return False, []
    prefixes = parsed.get("config", {}).get("ignore_pr_source_branches", [])
    if not isinstance(prefixes, list) or not all(isinstance(p, str) for p in prefixes):
        return True, []
    return True, prefixes


def _changed_files(base_ref: str) -> list[str] | None:
    """Every path that differs between origin/<base> and HEAD (the checked-out PR head), NUL
    separated so no filename is quoted out of the match. A `program` gitlink bump shows as the
    path `program`, which `_is_docs_only` never treats as docs. None if the diff itself failed —
    never read as "nothing changed"."""
    proc = _run_git(["diff", "-z", "--no-renames", "--name-only", f"origin/{base_ref}...HEAD"])
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode(errors="replace").split("\0") if p]


def _is_docs_only(path: str) -> bool:
    if path == "contract.pin" or path.startswith("contract/"):
        return False
    if path.startswith(DOCS_ONLY_DIRS):
        return True
    return path.endswith(".md")


def main() -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event != "pull_request":
        print(f"docs_branch_guard: {event or 'no'} event, not pull_request — no-op")
        return 0

    head_ref = os.environ.get("HEAD_REF", "")
    base_ref = os.environ.get("BASE_REF", "")
    if not head_ref or not base_ref:
        print("::error::docs_branch_guard: HEAD_REF/BASE_REF missing on a pull_request event",
              file=sys.stderr)
        return 1

    ok, prefixes = _prefixes_from_base(base_ref)
    if not ok:
        print(f"::error::docs_branch_guard: could not read or parse "
              f"origin/{base_ref}:.pr_agent.toml — failing closed", file=sys.stderr)
        return 1

    # re.match, as Qodo itself does: an unanchored match at the start of head_ref.
    if not any(re.match(p, head_ref) for p in prefixes):
        print(f"docs_branch_guard: '{head_ref}' matches no ignore_pr_source_branches prefix — "
              f"reviewed normally, nothing to enforce here")
        return 0

    changed = _changed_files(base_ref)
    if changed is None:
        print(f"::error::docs_branch_guard: git diff against origin/{base_ref} failed — cannot "
              f"tell what changed, so this is not a pass", file=sys.stderr)
        return 1

    offending = [p for p in changed if not _is_docs_only(p)]
    if offending:
        print(f"::error::docs_branch_guard: branch '{head_ref}' matches an "
              f"ignore_pr_source_branches prefix (Qodo skips review) but touches non-docs files: "
              f"{', '.join(offending)}", file=sys.stderr)
        return 1

    print(f"docs_branch_guard: '{head_ref}' skips Qodo review and touches only "
          f"{', '.join(DOCS_ONLY_DIRS)} or Markdown — consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
