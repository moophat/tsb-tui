"""Tests for leak-check.sh — the staged scan the pre-commit hook runs and CI fails the PR on.

Issue #459: reports from coding lanes carried three workstation path prefixes the pattern did not
catch. Each prefix is staged on its own line in a throwaway git repo and fed to a copy of the
script in `--staged --warn` mode; the warning output must name it. A plain line must stay clean,
and one line carrying an already-caught pattern proves the harness can fail at all.

The prefixes are assembled at runtime rather than written literally, so this file never matches
the pattern it tests and the full scan stays clean once it is tracked.

Uses pytest temporary directories and unittest assertions. Needs git; all repository operations
stay inside disposable local test directories.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest

import pytest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "leak-check.sh"
#: The three prefixes #459 adds, assembled so this file does not itself contain them.
PREFIXES = ("/opt/" + "lanes", "/opt/" + "tsb-test-venv", "/tmp/" + "opencode")
#: An already-caught pattern: the positive control that proves the harness reports a hit.
POSITIVE_CONTROL = "/root/" + ".secrets/probe"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )


class StagedPrefixDetection(unittest.TestCase):
    """A staged line holding a prefix warns; a clean line does not."""

    @pytest.fixture(autouse=True)
    def _temporary_root(self, tmp_path):
        self.temp_root = tmp_path

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=self.temp_root)
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # Keep the copy outside the scratch repo: the script names its own paths, and a staged
        # copy would be scanned along with the fixture line.
        self.script = self.root / "leak-check.sh"
        shutil.copy2(SCRIPT, self.script)

    def _run_warn(self, line: str) -> subprocess.CompletedProcess[str]:
        work = Path(tempfile.mkdtemp(dir=self.root))
        _git("init", "-q", str(work), cwd=self.root)
        (work / "notes.txt").write_text(line + "\n")
        _git("add", "-A", cwd=work)
        proc = subprocess.run(
            ["bash", str(self.script), "--staged", "--warn"],
            cwd=work, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def assert_reported(self, line: str, needle: str) -> None:
        proc = self._run_warn(line)
        self.assertIn("WARNING", proc.stderr)
        self.assertIn(needle, proc.stderr)

    def assert_clean(self, line: str) -> None:
        proc = self._run_warn(line)
        self.assertEqual(proc.stderr, "")
        self.assertIn("clean:", proc.stdout)

    def test_an_already_caught_pattern_is_reported(self):
        self.assert_reported(f"token file: {POSITIVE_CONTROL}", POSITIVE_CONTROL)

    def test_each_lane_prefix_is_reported_and_a_clean_line_is_not(self):
        for prefix in PREFIXES:
            with self.subTest(prefix=prefix):
                self.assert_reported(f"lane worktree: {prefix}/work", prefix)
                self.assert_clean("lane worktree: relative/place")


if __name__ == "__main__":
    unittest.main()
