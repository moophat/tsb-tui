"""Tests for build-engine.sh active build guard and systemctl failure handling."""

import os
import subprocess
import textwrap
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
BUILD_ENGINE = ENGINE / "build-engine.sh"


def make_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip())
    path.chmod(0o755)


def test_guard_refuses_before_docker_when_systemctl_fails(tmp_path):
    fake_bin = tmp_path / "bin"
    docker_log = tmp_path / "docker.log"

    make_executable(
        fake_bin / "systemctl",
        """
        #!/bin/sh
        echo "systemctl: connection refused" >&2
        exit 1
        """,
    )
    make_executable(
        fake_bin / "docker",
        f"""
        #!/bin/sh
        echo "docker called with $*" >> "{docker_log}"
        exit 0
        """,
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["TSB_VT_BUILD_HOST_ROLE"] = "integration-node"

    result = subprocess.run(
        ["bash", str(BUILD_ENGINE), "x86_64-windows-gnu"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "failed to query systemctl for active units" in result.stderr
    assert not docker_log.exists(), "script called docker instead of refusing at guard"


def test_guard_function_refuses_when_systemctl_fails(tmp_path):
    fake_bin = tmp_path / "bin"
    make_executable(
        fake_bin / "systemctl",
        """
        #!/bin/sh
        echo "systemctl: connection to bus failed" >&2
        exit 1
        """,
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "-c", f". '{BUILD_ENGINE}' && check_active_units"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "failed to query systemctl for active units" in result.stderr


def test_guard_function_passes_when_systemctl_succeeds_with_no_units(tmp_path):
    fake_bin = tmp_path / "bin"
    make_executable(
        fake_bin / "systemctl",
        """
        #!/bin/sh
        exit 0
        """,
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "-c", f". '{BUILD_ENGINE}' && check_active_units"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""


def test_guard_function_refuses_when_active_loop_running(tmp_path):
    fake_bin = tmp_path / "bin"
    make_executable(
        fake_bin / "systemctl",
        """
        #!/bin/sh
        echo "tsb-pml-worker.service active running"
        exit 0
        """,
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "-c", f". '{BUILD_ENGINE}' && check_active_units"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "another node build or post-merge loop is running" in result.stderr


def test_guard_function_ignores_current_build_unit(tmp_path):
    fake_bin = tmp_path / "bin"
    make_executable(
        fake_bin / "systemctl",
        """
        #!/bin/sh
        echo "tsb-engine-win-current.service active running"
        exit 0
        """,
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["TSB_ENGINE_BUILD_UNIT"] = "tsb-engine-win-current.service"

    result = subprocess.run(
        ["bash", "-c", f". '{BUILD_ENGINE}' && check_active_units"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""
