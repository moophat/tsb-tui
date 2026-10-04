"""Puts tui's pinned contract tree on sys.path before any test module imports `tsbc` or
`tsb_contract_client` — tui/CONTRACT_VERSION names the tag, materialized under `.contract-pins/`
(gitignored), never the working tree's `contract/` (development-plan.md §1: a contract PR in
flight must not turn tui/'s tests red before tui/ bumps its own pin). CI no longer installs
`contract/` into this venv (`-e ./contract` dropped, .github/workflows/test.yml) — without this,
collecting any test module that imports tsb_tui (which imports tsbc via _transport.py) fails
outright. pytest imports every conftest.py before collecting the test modules in its directory, so
this runs first.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

TUI_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TUI_DIR.parent
PIN_FILE = TUI_DIR / "CONTRACT_VERSION"

# Every pin file's own format, checked before anything else touches disk: an unvalidated pin
# could put ".." in a path this joins with a directory, or reach `git` with an argument that is
# not a ref at all.
_TAG_RE = re.compile(r"^contract-v[0-9]+\.[0-9]+(\.[0-9]+)?$")
# What `git rev-parse <tag>:contract` must print on success: a full hex object id, read from
# stdout only (stderr is kept apart, for the error message) and checked before any of it is
# trusted or truncated.
_TREE_ID_RE = re.compile(r"^[0-9a-f]{40,}$")
# How many hex characters of that id key a cache entry — the same length in every materialiser
# (Go, Python, bash), so a re-cut tag (the same name, a different tree) never collides with an
# entry a different process already trusts.
_TREE_ID_LEN = 12


def _run_git(args: list[str], repo_root: Path, pin_file: Path, tag: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, check=False)
    except FileNotFoundError as e:
        raise RuntimeError(f"git is not installed or not on PATH — needed to resolve {pin_file}'s "
                            f"tag {tag}: {e}") from e


def _resolve_tree(repo_root: Path, pin_file: Path, tag: str) -> str:
    """The full hex object id of tag's contract/ tree, resolved fresh on every call — never cached
    across calls, so a tag moved after an earlier process cached its tree (a re-cut tag) is caught
    here, by keying a new cache entry, rather than silently reused. stdout and stderr are captured
    separately (subprocess.run's own fields), so a stray stderr line can never end up inside the id
    this parses."""
    proc = _run_git(["rev-parse", f"{tag}:contract"], repo_root, pin_file, tag)
    if proc.returncode != 0:
        raise RuntimeError(
            f"resolving tag {tag} (named by {pin_file}) failed — does the tag exist? "
            f"(git fetch --tags): {proc.stderr.decode(errors='replace')}")
    tree = proc.stdout.decode().strip()
    if not _TREE_ID_RE.match(tree):
        raise RuntimeError(f"git rev-parse {tag}:contract (named by {pin_file}) printed {tree!r}, not a tree id")
    return tree


def _chown_if_sudo(pins_dir: Path, entry_dir: Path) -> None:
    """When running as root under sudo, hands a freshly materialized entry back to the real
    operator: pins_dir itself (non-recursive, so a later non-root process can create sibling
    entries) and entry_dir (recursive, its whole new tree). Best-effort on pins_dir — it may
    already belong to an earlier materialisation's operator, which is fine; a failure to chown the
    entry this process just created is not swallowed, matching the bash and Go materialisers."""
    if os.geteuid() != 0:
        return
    sudo_uid = os.environ.get("SUDO_UID")
    if not sudo_uid:
        return
    uid = int(sudo_uid)
    gid = int(os.environ.get("SUDO_GID") or sudo_uid)
    try:
        os.chown(pins_dir, uid, gid)
    except OSError:
        pass
    os.chown(entry_dir, uid, gid)
    for dirpath, dirnames, filenames in os.walk(entry_dir):
        for name in dirnames + filenames:
            os.chown(os.path.join(dirpath, name), uid, gid)


def _materialize_pin() -> Path:
    """tui's own copy of the materialize-and-self-check logic (no shared helper across unit
    directories — worker/'s and harness's each carry theirs too, so contract/ and tui/ stay
    separable by `git subtree split`). Keyed by <tag>@<tree> so a re-cut tag gets a new entry
    instead of being served stale; the archive itself is taken from that same resolved tree object
    (`git archive <tree-id>`), never the tag name again, so nothing can move the tag between the
    resolve and the archive and hand two different materialisations the same key. A tree object
    archives with its entries at the root (no `contract/` prefix), so the extraction target is the
    entry's contract/ directory itself."""
    tag = PIN_FILE.read_text().strip()
    if not _TAG_RE.match(tag):
        raise RuntimeError(f"{PIN_FILE} is {tag!r}: want a contract-vX.Y or contract-vX.Y.Z tag")
    tree = _resolve_tree(REPO_ROOT, PIN_FILE, tag)
    entry = f"{tag}@{tree[:_TREE_ID_LEN]}"
    pins_dir = REPO_ROOT / ".contract-pins"
    entry_dir = pins_dir / entry
    contract_dir = entry_dir / "contract"
    if not contract_dir.is_dir():
        entry_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f"{entry}.tmp-", dir=pins_dir))
        try:
            proc = _run_git(["archive", tree], REPO_ROOT, PIN_FILE, tag)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"git archive {tree} (tag {tag}, named by {PIN_FILE}) failed: "
                    f"{proc.stderr.decode(errors='replace')}")
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
                tar.extractall(tmp, filter="data")
            try:
                tmp.rename(contract_dir)
            except OSError:
                if not contract_dir.is_dir():
                    raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        # Whichever process's rename actually landed, this one still asked to create the entry —
        # chowning here (not on the plain cache-hit path above) matches the bash materialiser.
        _chown_if_sudo(pins_dir, entry_dir)
    got = (contract_dir / "CONTRACT_VERSION").read_text().strip()
    if got != tag:
        raise RuntimeError(
            f"materialized {contract_dir}/CONTRACT_VERSION is {got!r}: {PIN_FILE} names {tag}")
    return contract_dir


CONTRACT_DIR = _materialize_pin()
for _entry in (CONTRACT_DIR, CONTRACT_DIR / "client"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))
