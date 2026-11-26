"""Synthetic contract bump sequence verification (acceptance 02-A0)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _git(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    base_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "tsb-release",
        "GIT_AUTHOR_EMAIL": "release@example.test",
        "GIT_COMMITTER_NAME": "tsb-release",
        "GIT_COMMITTER_EMAIL": "release@example.test",
    }
    if env:
        base_env.update(env)
    return subprocess.run(
        ["git", *cmd],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=base_env,
    )


class ContractBumpSequenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # 1. Bare tsb-program.git with two release tags
        self.program_bare = self.root / "tsb-program.git"
        _git(["init", "--bare", str(self.program_bare)], cwd=self.root)

        prog_work = self.root / "prog-work"
        _git(["init", "-b", "main", str(prog_work)], cwd=self.root)
        _git(["remote", "add", "origin", str(self.program_bare)], cwd=prog_work)

        # Tag 1: v0.1.0
        contract_dir = prog_work / "contract"
        srs_dir = prog_work / "srs"
        contract_dir.mkdir(parents=True)
        srs_dir.mkdir(parents=True)
        (contract_dir / "CONTRACT_VERSION").write_text("v0.1.0\n")
        (contract_dir / "README.md").write_text("# Contract v0.1.0\n")
        (srs_dir / "architecture-contract.md").write_text("# SRS\n")
        _git(["add", "-A"], cwd=prog_work)
        _git(["commit", "-m", "release v0.1.0"], cwd=prog_work)
        _git(["tag", "-a", "v0.1.0", "-m", "v0.1.0 tag tsb-release"], cwd=prog_work)
        _git(["push", "--tags", "origin", "main"], cwd=prog_work)

        # Tag 2: v0.2.0 with altered contract tree
        (contract_dir / "CONTRACT_VERSION").write_text("v0.2.0\n")
        (contract_dir / "new_field.json").write_text('{"field": true}\n')
        _git(["add", "-A"], cwd=prog_work)
        _git(["commit", "-m", "release v0.2.0"], cwd=prog_work)
        _git(["tag", "-a", "v0.2.0", "-m", "v0.2.0 tag tsb-release"], cwd=prog_work)
        _git(["push", "--tags", "origin", "main"], cwd=prog_work)

    def _setup_consumer_repo(self) -> Path:
        """Create a consumer repository skeleton at v0.1.0."""
        work = Path(tempfile.mkdtemp(dir=self.root))
        _git(["init", "-b", "main", str(work)], cwd=self.root)

        # Copy tools and Makefile
        tools_dir = work / "tools"
        tools_dir.mkdir()
        shutil.copy2(REPO_ROOT / "tools/contract-pin.sh", tools_dir / "contract-pin.sh")
        (tools_dir / "contract-pin.sh").chmod(0o755)
        shutil.copy2(REPO_ROOT / "Makefile", work / "Makefile")

        # Setup .gitmodules
        gitmodules = work / ".gitmodules"
        gitmodules.write_text(
            '[submodule "contract"]\n'
            "\tpath = program\n"
            f"\turl = ../tsb-program.git\n"
            "\tupdate = none\n"
        )

        # Submodule init at v0.1.0
        v1_sha = _git(["rev-parse", "refs/tags/v0.1.0^{commit}"], cwd=self.program_bare).stdout.strip()
        _git(["update-index", "--add", "--cacheinfo", f"160000,{v1_sha},program"], cwd=work)
        _git(["config", "submodule.contract.url", str(self.program_bare)], cwd=work)
        _git(["config", "submodule.contract.update", "none"], cwd=work)

        # Run make contract-init and contract-copy to populate v0.1.0
        subprocess.run(["bash", "tools/contract-pin.sh", "init"], cwd=work, check=True)
        subprocess.run(["bash", "tools/contract-pin.sh", "copy"], cwd=work, check=True)

        _git(["add", "-A"], cwd=work)
        _git(["commit", "-m", "initial consumer at v0.1.0"], cwd=work)

        # Verify initial check passes
        subprocess.run(["bash", "tools/contract-pin.sh", "check"], cwd=work, check=True,
                       env={**os.environ, "CONTRACT_COMMITTED": "1"})
        return work

    def test_offline_copy_check_rejects_fabricated_commit(self):
        work = self._setup_consumer_repo()
        shutil.rmtree(work / "program")
        pin = work / "contract.pin"
        lines = pin.read_text().splitlines()
        pin.write_text("\n".join("commit: " + "0" * 40 if line.startswith("commit:") else line
                                 for line in lines) + "\n")
        _git(["add", "contract.pin"], cwd=work)
        result = subprocess.run(["bash", "tools/contract-pin.sh", "copy-check"],
                                cwd=work, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match gitlink", result.stderr)

    def test_offline_copy_check_without_submodule_succeeds(self):
        work = self._setup_consumer_repo()
        shutil.rmtree(work / "program")
        result = subprocess.run(["bash", "tools/contract-pin.sh", "copy-check"],
                                cwd=work, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_documented_seven_step_bump_succeeds(self):
        work = self._setup_consumer_repo()

        # Step 1: inside program/, fetch and checkout v0.2.0
        _git(["fetch", "origin", "--tags"], cwd=work / "program")
        _git(["checkout", "--detach", "v0.2.0"], cwd=work / "program")

        # Step 2: git add program
        _git(["add", "program"], cwd=work)

        # Step 3: make contract-init contract-copy
        subprocess.run(["bash", "tools/contract-pin.sh", "init"], cwd=work, check=True)
        subprocess.run(["bash", "tools/contract-pin.sh", "copy"], cwd=work, check=True)

        # Step 4: git add contract contract.pin
        _git(["add", "contract", "contract.pin"], cwd=work)

        # Step 5: make contract-check (assert exit 0)
        proc5 = subprocess.run(["bash", "tools/contract-pin.sh", "check"], cwd=work, capture_output=True, text=True)
        self.assertEqual(proc5.returncode, 0, f"Step 5 failed: {proc5.stderr}")

        # Step 6: commit
        _git(["commit", "-m", "bump to v0.2.0"], cwd=work)

        # Step 7: CONTRACT_COMMITTED=1 make contract-check (assert exit 0)
        proc7 = subprocess.run(["bash", "tools/contract-pin.sh", "check"], cwd=work, capture_output=True, text=True,
                               env={**os.environ, "CONTRACT_COMMITTED": "1"})
        self.assertEqual(proc7.returncode, 0, f"Step 7 failed: {proc7.stderr}")

    def test_variant_step_4_skipped_fails_step_5(self):
        work = self._setup_consumer_repo()

        # Step 1, 2, 3
        _git(["fetch", "origin", "--tags"], cwd=work / "program")
        _git(["checkout", "--detach", "v0.2.0"], cwd=work / "program")
        _git(["add", "program"], cwd=work)
        subprocess.run(["bash", "tools/contract-pin.sh", "init"], cwd=work, check=True)
        subprocess.run(["bash", "tools/contract-pin.sh", "copy"], cwd=work, check=True)

        # Step 4 SKIPPED: do not git add contract contract.pin

        # Step 5: make contract-check must fail
        proc5 = subprocess.run(["bash", "tools/contract-pin.sh", "check"], cwd=work, capture_output=True, text=True)
        self.assertNotEqual(proc5.returncode, 0, "Step 5 should fail when step 4 is skipped")

    def test_variant_step_3_skipped_fails_step_5(self):
        work = self._setup_consumer_repo()

        # Step 1, 2
        _git(["fetch", "origin", "--tags"], cwd=work / "program")
        _git(["checkout", "--detach", "v0.2.0"], cwd=work / "program")
        _git(["add", "program"], cwd=work)

        # Step 3 SKIPPED: do not run make contract-copy

        # Step 4
        _git(["add", "contract", "contract.pin"], cwd=work)

        # Step 5: make contract-check must fail
        proc5 = subprocess.run(["bash", "tools/contract-pin.sh", "check"], cwd=work, capture_output=True, text=True)
        self.assertNotEqual(proc5.returncode, 0, "Step 5 should fail when step 3 is skipped")

    def test_variant_commit_made_before_step_4_fails_step_7(self):
        work = self._setup_consumer_repo()

        # Step 1, 2, 3
        _git(["fetch", "origin", "--tags"], cwd=work / "program")
        _git(["checkout", "--detach", "v0.2.0"], cwd=work / "program")
        _git(["add", "program"], cwd=work)
        subprocess.run(["bash", "tools/contract-pin.sh", "init"], cwd=work, check=True)
        subprocess.run(["bash", "tools/contract-pin.sh", "copy"], cwd=work, check=True)

        # Commit made BEFORE step 4
        _git(["commit", "-m", "premature commit"], cwd=work)

        # Even if step 4 is run now, step 7 with CONTRACT_COMMITTED=1 must fail because commit does not hold the copy
        _git(["add", "contract", "contract.pin"], cwd=work)
        proc7 = subprocess.run(["bash", "tools/contract-pin.sh", "check"], cwd=work, capture_output=True, text=True,
                               env={**os.environ, "CONTRACT_COMMITTED": "1"})
        self.assertNotEqual(proc7.returncode, 0, "Step 7 should fail when commit was made before step 4")


if __name__ == "__main__":
    unittest.main()
