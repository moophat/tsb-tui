"""Tests for docs_branch_guard.py — the check that proves a PR whose branch skips Qodo review
(`.pr_agent.toml`'s `ignore_pr_source_branches`) actually stays docs-only. A guard that stops
catching what it exists to catch must fail this job, not pass it — `test.yml`'s own early step
runs this file first, before invoking the guard for real (`tools/test_import_lint.py`'s own
convention for a repo gate).

Uses pytest for its temporary directory and unittest assertions; run with
`python3 -m pytest .github/scripts/tests`. All Git operations use disposable local repositories.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

import pytest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]

PR_AGENT_TOML = (
    '[config]\n'
    'ignore_pr_source_branches = ["^plan/", "^chore/ledger", "^docs/", "^research/"]\n'
)


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )


class DocsBranchGuard(unittest.TestCase):
    """A scratch bare `origin` plus a work clone: `origin/main` stands in for the PR's base branch
    (`git show origin/<base>:...`, `git diff origin/<base>...HEAD` — exactly what a real checkout
    with `fetch-depth: 0` leaves behind), and each test's own commits on top of it, never pushed,
    stand in for the PR's own head."""

    @pytest.fixture(autouse=True)
    def _temporary_root(self, tmp_path):
        self.temp_root = tmp_path

    def setUp(self):
        self.work = self._fresh_repo()

    def _fresh_repo(self) -> Path:
        """A new scratch bare `origin` plus a work clone, independent of `self.work` — used both
        for the default per-test repo and for the multi-path subTest loop below, which needs a
        clean diff for each path rather than accumulating commits in one repo."""
        tmp = tempfile.TemporaryDirectory(dir=self.temp_root)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        origin = root / "origin.git"
        work = root / "work"
        git("init", "-q", "--bare", "-b", "main", str(origin), cwd=root)
        git("init", "-q", "-b", "main", str(work), cwd=root)
        (work / ".pr_agent.toml").write_text(PR_AGENT_TOML)
        git("add", "-A", cwd=work)
        git("commit", "-qm", "base", cwd=work)
        git("remote", "add", "origin", str(origin), cwd=work)
        git("push", "-q", "origin", "main", cwd=work)
        git("fetch", "-q", "origin", cwd=work)
        return work

    def _commit(self, files: dict[str, str], *, work: Path | None = None) -> None:
        work = work or self.work
        for rel, body in files.items():
            path = work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        git("add", "-A", cwd=work)
        git("commit", "-qm", "change", cwd=work)

    def _commit_gitlink(self, path: str, *, work: Path | None = None) -> None:
        """Record `path` as a submodule gitlink (mode 160000) and commit it — the shape a pin bump
        has. No working-tree file is written, matching a gitlink's own diff."""
        work = work or self.work
        git("update-index", "--add", "--cacheinfo",
            f"160000,{'1' * 40},{path}", cwd=work)
        git("commit", "-qm", f"bump {path}", cwd=work)

    def _run(self, *, event: str, head_ref: str = "", base_ref: str = "main",
              work: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["GITHUB_EVENT_NAME"] = event
        env["HEAD_REF"] = head_ref
        env["BASE_REF"] = base_ref
        return subprocess.run([sys.executable, str(SCRIPTS / "docs_branch_guard.py")],
                               cwd=work or self.work, env=env, capture_output=True, text=True,
                               check=False)

    def test_docs_branch_with_a_docs_only_diff_passes(self):
        self._commit({"plans/change.md": "notes\n"})
        proc = self._run(event="pull_request", head_ref="plan/my-plan")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_docs_branch_with_workflow_markdown_passes(self):
        self._commit({".aiworkflow/notes.md": "notes\n"})
        proc = self._run(event="pull_request", head_ref="docs/workflow")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_docs_branch_with_non_markdown_workflow_files_fails(self):
        for path in (".aiworkflow/rotate-work-tracker.py", ".aiworkflow/helper.py", ".aiworkflow/queue.txt"):
            with self.subTest(path=path):
                work = self._fresh_repo()
                self._commit({path: "code\n"}, work=work)
                proc = self._run(event="pull_request", head_ref="docs/workflow", work=work)
                self.assertEqual(proc.returncode, 1)
                self.assertIn(path, proc.stderr)

    def test_docs_branch_touching_code_fails_naming_the_file(self):
        # Why it matters: a branch Qodo never reviews (by name alone) must not be able to slip a
        # real code change through on green CI just by living on a plan/ branch. Each path gets its
        # own fresh repo so one offending file's diff is never muddied by another's.
        for path in ("tsb_tui/app.py", "tests/test_app.py", "tools/check-engine.sh", "Makefile", "contract.pin", "contract/README.md"):
            with self.subTest(path=path):
                work = self._fresh_repo()
                self._commit({path: "x\n"}, work=work)
                proc = self._run(event="pull_request", head_ref="plan/my-plan", work=work)
                self.assertEqual(proc.returncode, 1)
                self.assertIn(path, proc.stderr)

    def test_docs_branch_bumping_the_program_gitlink_fails(self):
        # Why it matters: a bump of the `program` submodule is a commit hash moving, not docs — a
        # branch Qodo never reviews must not slip it through by carrying a `.md`-shaped name or by
        # living under a docs prefix. The gitlink shows in the diff as the path `program`.
        self._commit_gitlink("program", work=self.work)
        proc = self._run(event="pull_request", head_ref="docs/pin-bump")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("program", proc.stderr)

    def test_feat_branch_bumping_the_program_gitlink_passes(self):
        # The same bump on a normal branch is reviewed like any other PR: the guard only enforces
        # the branches Qodo actually skips.
        self._commit_gitlink("program", work=self.work)
        proc = self._run(event="pull_request", head_ref="feat/pin-bump")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_non_docs_branch_with_code_passes(self):
        # Why it matters: this check only enforces the branches Qodo actually skips — a normal
        # branch is reviewed as usual, and this must never block it.
        self._commit({"tsb_tui/app.py": "value = 1\n"})
        proc = self._run(event="pull_request", head_ref="feat/my-feature")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_base_pr_agent_toml_with_no_ignore_key_yet_is_not_a_failure(self):
        # Why it matters: this is the real state of `main` until the PR that first adds
        # ignore_pr_source_branches merges — a valid .pr_agent.toml that simply predates the key.
        # Treating that the same as unparseable would fail this exact PR's own required check
        # forever, since main cannot carry a key that has not merged yet. No prefix is configured,
        # so nothing is exempted from Qodo review — nothing for this guard to enforce either.
        (self.work / ".pr_agent.toml").write_text('[config]\nallow_only_specific_folders = ["tsb_tui"]\n')
        git("add", "-A", cwd=self.work)
        git("commit", "-qm", "pre-amendment pr_agent.toml", cwd=self.work)
        git("push", "-q", "origin", "main", cwd=self.work)
        self._commit({"tsb_tui/app.py": "value = 1\n"})
        proc = self._run(event="pull_request", head_ref="plan/my-plan")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_base_with_confirmed_absent_config_exempts_no_branch(self):
        git("rm", ".pr_agent.toml", cwd=self.work)
        git("commit", "--allow-empty", "-qm", "base without config", cwd=self.work)
        git("push", "-q", "origin", "main", cwd=self.work)
        self._commit({"tsb_tui/app.py": "value = 1\n"})
        proc = self._run(event="pull_request", head_ref="docs/bootstrap")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("reviewed normally", proc.stdout)

    def test_git_error_loading_the_base_fails_closed(self):
        proc = self._run(event="pull_request", head_ref="docs/bootstrap", base_ref="missing")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("failing closed", proc.stderr)

    def test_docs_directory_is_allowed_but_research_code_is_not(self):
        self._commit({"docs/guide.txt": "notes\n", ".aiworkflow/queue.md": "notes\n"})
        proc = self._run(event="pull_request", head_ref="docs/guide")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._commit({"research/code.py": "value = 1\n"})
        proc = self._run(event="pull_request", head_ref="docs/guide")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("research/code.py", proc.stderr)

    def test_unparseable_toml_on_the_base_branch_fails_closed(self):
        (self.work / ".pr_agent.toml").write_text("not valid toml [[[")
        git("add", "-A", cwd=self.work)
        git("commit", "-qm", "break toml", cwd=self.work)
        git("push", "-q", "origin", "main", cwd=self.work)
        proc = self._run(event="pull_request", head_ref="plan/my-plan")
        self.assertEqual(proc.returncode, 1)

    def test_push_event_is_a_no_op(self):
        proc = self._run(event="push")
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
