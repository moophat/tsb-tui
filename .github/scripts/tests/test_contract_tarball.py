"""Contract tarball and wheel verification tests (acceptance 02-G and 02-F)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED = {"openapi", "schemas", "fixtures", "protocols", "tsbc", "client", "README.md", "CONTRACT_VERSION", "pyproject.toml", "LICENSE"}


class ContractTarballAndWheelTest(unittest.TestCase):
    def test_tarball_dry_run_builds_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "dist"
            version_text = (REPO_ROOT / "contract/CONTRACT_VERSION").read_text().strip()
            expected_name = f"tsb-{version_text}.tar.gz"

            proc = subprocess.run(
                ["bash", "contract/tools/build-tarball.sh", str(out_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env={**os.environ, "ALLOW_DIRTY": "1"},
            )
            self.assertEqual(proc.returncode, 0, f"build-tarball failed: {proc.stderr}")

            tarball_path = out_dir / expected_name
            self.assertTrue(tarball_path.is_file(), f"Expected tarball {expected_name} not found")

            # Unpack and verify structure
            unpack_dir = Path(tmp_dir) / "unpacked"
            unpack_dir.mkdir()
            with tarfile.open(tarball_path, "r:gz") as tar:
                tar.extractall(unpack_dir, filter="data")

            top_dir = unpack_dir / f"tsb-{version_text}"
            self.assertTrue(top_dir.is_dir())
            top_members = {p.name for p in top_dir.iterdir()}
            self.assertEqual(top_members, SHIPPED, f"Tarball members do not match shipped list: {top_members}")

            # Run tsbc verify --self-check on the unpacked contents
            verify_proc = subprocess.run(
                [
                    "python3",
                    "-m",
                    "tsbc",
                    "verify",
                    "--self-check",
                    "--contract-dir",
                    str(top_dir),
                ],
                cwd=top_dir,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": str(top_dir)},
            )
            self.assertEqual(verify_proc.returncode, 0, f"self-check failed: {verify_proc.stdout}\n{verify_proc.stderr}")

    def test_contract_wheel_builds_from_the_copy_alone(self):
        """Needs nothing but `contract/`, so it runs on a clone that has no `program/`."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist = Path(tmp_dir) / "dist"
            dist.mkdir()
            copy = Path(tmp_dir) / "contract_copy"
            shutil.copytree(REPO_ROOT / "contract", copy)
            subprocess.run(
                ["pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(dist), str(copy)],
                check=True,
                capture_output=True,
            )
            wheels = list(dist.glob("tsb_contract-*.whl"))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as z:
                names = z.namelist()
            self.assertTrue(any(n.startswith("tsbc/") for n in names), "the wheel carries no tsbc package")
            self.assertTrue(any(n.startswith("tsb_contract_client/") for n in names),
                            "the wheel carries no tsb_contract_client package")

    @unittest.skipUnless(
        (REPO_ROOT / "program/contract/CONTRACT_VERSION").is_file(),
        "program/contract is not populated (the private program checkout is absent; run make contract-init)",
    )
    def test_contract_wheel_matches_program_contract_wheel(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist1 = Path(tmp_dir) / "dist1"
            dist2 = Path(tmp_dir) / "dist2"
            dist1.mkdir()
            dist2.mkdir()

            copy1 = Path(tmp_dir) / "contract_copy1"
            copy2 = Path(tmp_dir) / "contract_copy2"
            shutil.copytree(REPO_ROOT / "contract", copy1)
            shutil.copytree(REPO_ROOT / "program/contract", copy2)

            subprocess.run(
                ["pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(dist1), str(copy1)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(dist2), str(copy2)],
                check=True,
                capture_output=True,
            )

            wheels1 = list(dist1.glob("tsb_contract-*.whl"))
            wheels2 = list(dist2.glob("tsb_contract-*.whl"))
            self.assertEqual(len(wheels1), 1)
            self.assertEqual(len(wheels2), 1)
            self.assertEqual(wheels1[0].name, wheels2[0].name)

            # Compare member files and contents
            with zipfile.ZipFile(wheels1[0]) as z1, zipfile.ZipFile(wheels2[0]) as z2:
                names1 = sorted(z1.namelist())
                names2 = sorted(z2.namelist())
                self.assertEqual(names1, names2, "Wheel member file lists differ")
                for name in names1:
                    # Exclude RECORD if timestamps or hashes differ slightly in wheel packaging metadata
                    if not name.endswith(".dist-info/RECORD"):
                        self.assertEqual(z1.read(name), z2.read(name), f"File {name} differs between wheels")


if __name__ == "__main__":
    unittest.main()
