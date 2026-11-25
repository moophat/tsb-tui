"""Verify that the release tarball ships its Apache-2.0 licence."""

import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from . import CONTRACT


def build(tree: Path, out: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, ALLOW_DIRTY="1", SOURCE_DATE_EPOCH="1789000000")
    return subprocess.run(
        ["bash", str(tree / "tools" / "build-tarball.sh"), str(out)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


class TarballLicence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.version = (CONTRACT / "CONTRACT_VERSION").read_text().strip()

    def test_tarball_ships_apache2_licence(self):
        out = self.dir / "dist"
        result = build(CONTRACT, out)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

        tarball = out / f"tsb-{self.version}.tar.gz"
        self.assertTrue(tarball.is_file(), f"missing archive {tarball}")

        with tarfile.open(tarball, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn(f"tsb-{self.version}/LICENSE", names)
            unpacked = self.dir / "unpacked"
            unpacked.mkdir()
            tar.extractall(unpacked, filter="data")

        unpacked_tree = unpacked / f"tsb-{self.version}"
        shipped = (unpacked_tree / "LICENSE").read_bytes()
        self.assertEqual(shipped, (CONTRACT / "LICENSE").read_bytes())
        self.assertIn(b"Apache License", shipped)
        self.assertIn(b"Version 2.0, January 2004", shipped)

        check = subprocess.run(
            [sys.executable, "-m", "tsbc", "verify", "--self-check"],
            cwd=str(unpacked_tree),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(check.returncode, 0, check.stderr + check.stdout)


if __name__ == "__main__":
    unittest.main()
