"""tools/build-tarball.sh: what strangers receive.

The tarball is the only artefact that leaves the repo, so it must hold the shipped paths and
nothing else, be reproducible (the same tree gives the same bytes), refuse an untagged version,
and refuse to pack at all when self-check finds key material.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from . import CONTRACT

SCRIPT = CONTRACT / "tools" / "build-tarball.sh"
SHIPPED = ("openapi/", "schemas/", "fixtures/", "protocols/", "tsbc/", "client/", "README.md", "CONTRACT_VERSION", "pyproject.toml", "LICENSE")
PEM = "-----BEGIN " + "OPENSSH PRIVATE KEY" + "-----"


def build(tree: Path, out: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, ALLOW_DIRTY="1", SOURCE_DATE_EPOCH="1789000000")
    return subprocess.run(["bash", str(tree / "tools" / "build-tarball.sh"), str(out)],
                          capture_output=True, text=True, env=env, timeout=120, check=False)


def copy_tree(dest: Path) -> Path:
    tree = dest / "contract"
    shutil.copytree(CONTRACT, tree, ignore=shutil.ignore_patterns("__pycache__"))
    return tree


def set_version(tree: Path, version: str) -> None:
    """Rewrite CONTRACT_VERSION and the openapi info.version together, so the copied tree stays a
    self-consistent contract: the self-check requires them to match."""
    (tree / "CONTRACT_VERSION").write_text(version + "\n")
    spec = tree / "openapi" / "tsb.openapi.json"
    doc = json.loads(spec.read_text(encoding="utf-8"))
    doc["info"]["version"] = version
    spec.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class Tarball(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.version = (CONTRACT / "CONTRACT_VERSION").read_text().strip()

    def test_ships_only_the_published_paths_reproducibly(self):
        if self.version.endswith("-dev"):
            self.skipTest("CONTRACT_VERSION is a -dev version; the builder refuses it by design")
        digests = []
        for run in ("a", "b"):
            result = build(CONTRACT, self.dir / run)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            target = self.dir / run / f"tsb-{self.version}.tar.gz"
            digests.append(hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(digests[0], digests[1], "two builds of one tree differ")
        top = f"tsb-{self.version}/"
        with tarfile.open(self.dir / "a" / f"tsb-{self.version}.tar.gz") as tar:
            names = tar.getnames()
            members = tar.getmembers()
        inner = [n[len(top):] for n in names if n.startswith(top)]
        self.assertEqual(len(inner), len(names) - 1)  # everything under one top directory
        for name in inner:
            self.assertTrue(any(name == s.rstrip("/") or name.startswith(s) for s in SHIPPED), name)
            self.assertNotIn("__pycache__", name)
            self.assertFalse(any(part.startswith(".") for part in name.split("/")), name)
        self.assertIn("tsbc/verify.py", inner)
        self.assertIn("client/tsb_contract_client/client.py", inner)
        self.assertIn("fixtures/C3/data-frame-envelope.bin", inner)
        self.assertFalse(any(n.startswith(("tests", "tools")) for n in inner))
        self.assertTrue(all(m.uid == 0 and m.gid == 0 and m.mtime == 1789000000 for m in members))

    def test_refuses_a_dev_version(self):
        tree = copy_tree(self.dir)
        set_version(tree, "contract-v0.1-dev")
        result = build(tree, self.dir / "out")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contract-vX.Y", result.stderr)
        self.assertFalse((self.dir / "out").exists() and any((self.dir / "out").iterdir()))

    def test_packs_a_patch_version(self):
        # A patch tag (contract-vX.Y.Z) is a release like any other: a grader-only patch ships
        # without pretending to be a new minor, so the builder and the self-check accept it.
        tree = copy_tree(self.dir)
        set_version(tree, "contract-v0.2.1")
        result = build(tree, self.dir / "out")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.dir / "out" / "tsb-contract-v0.2.1.tar.gz").exists())

    def test_packs_a_plain_release_tag(self):
        # tsb-program names the contract's releases with a plain version (vX.Y.Z); the builder
        # accepts that form beside contract-vX.Y[.Z], so the tarball is named after it.
        tree = copy_tree(self.dir)
        set_version(tree, "v0.3.1")
        result = build(tree, self.dir / "out")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.dir / "out" / "tsb-v0.3.1.tar.gz").exists())

    def test_refuses_a_version_that_is_not_a_tag(self):
        tree = copy_tree(self.dir)
        set_version(tree, "contract-v0.2.1.4")
        result = build(tree, self.dir / "out")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contract-vX.Y", result.stderr)

    def test_refuses_a_shipped_path_that_is_a_symlink(self):
        # is_file() and read_bytes() follow links, so a linked README or client directory would pack
        # whatever the link points at on the builder's host.
        for name in ("README.md", "client"):
            with self.subTest(name=name):
                tree = copy_tree(self.dir / f"linked-{name}")
                set_version(tree, "contract-v0.1")
                outside = self.dir / f"outside-{name}"
                shutil.move(str(tree / name), str(outside))
                (tree / name).symlink_to(outside, target_is_directory=outside.is_dir())
                out = self.dir / f"out-{name}"
                result = build(tree, out)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(f"{name} is a symlink", result.stderr)
                self.assertFalse(list(out.glob("*.tar.gz")) if out.exists() else [])

    def test_refuses_to_pack_key_material(self):
        tree = copy_tree(self.dir)
        set_version(tree, "contract-v0.1")
        (tree / "fixtures" / "C6" / "leak.bin").write_bytes(PEM.encode())
        result = build(tree, self.dir / "out")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing was packed", result.stderr)
        self.assertFalse(list((self.dir / "out").glob("*.tar.gz")) if (self.dir / "out").exists() else [])


if __name__ == "__main__":
    unittest.main()
