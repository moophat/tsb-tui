"""Release assets install without a checkout; pipx and GitHub calls stay in test doubles."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TUI = "tsb_tui-1.2.3-py3-none-any.whl"
CONTRACT = "tsb_contract-0.12.1-py3-none-any.whl"
ENGINE = "libghostty-vt-linux-x86_64.tar.gz"
WINDOWS_ZIP = "tsb-tui-1.2.3-windows-x64.zip"
PAYLOADS = [TUI, CONTRACT, ENGINE, "install.sh", "install.ps1"]


def executable(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip())
    path.chmod(0o755)


def fake_interpreter(path, ok):
    """A PATH interpreter that passes (or fails) install.sh's `sys.version_info >= (3, 12)` check."""
    executable(path, f"""
        #!/bin/bash
        exit {0 if ok else 1}
    """)


def system_path(bin_dir, monkeypatch):
    """PATH holding only bin_dir plus the system tools install.sh needs, so no real interpreter is found."""
    tools = bin_dir.parent / "system-tools"
    tools.mkdir()
    for tool in ("bash", "awk", "sha256sum", "tar", "mkdir"):
        found = shutil.which(tool)
        assert found, tool
        (tools / tool).symlink_to(found)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{tools}")


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    assets = tmp_path / "downloaded assets"
    assets.mkdir()
    venvs = tmp_path / "fake venvs"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    log = assets / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CALL_LOG", str(log))
    monkeypatch.setenv("FAKE_VENVS", str(venvs))
    monkeypatch.setenv("FAKE_BIN_DIR", str(bin_dir))
    monkeypatch.setenv("FAKE_GLIBC", "glibc 2.29")
    executable(bin_dir / "getconf", """
        #!/bin/bash
        [[ "$1" == GNU_LIBC_VERSION ]] || exit 1
        printf '%s\\n' "$FAKE_GLIBC"
    """)
    executable(bin_dir / "uname", """
        #!/bin/bash
        printf 'x86_64\\n'
    """)
    # Only the two newest candidates are faked, so the fake pipx's `#!/usr/bin/env python3`
    # shebang still resolves to the interpreter running these tests.
    for name in ("python3.13", "python3.12"):
        fake_interpreter(bin_dir / name, True)
    executable(bin_dir / "pipx", """
        #!/usr/bin/env python3
        import json, os, sys
        from pathlib import Path
        with open(os.environ['CALL_LOG'], 'a') as log:
            log.write(json.dumps(sys.argv[1:]) + '\\n')
        if sys.argv[1:] == ['environment', '--value', 'PIPX_LOCAL_VENVS']:
            print(os.environ['FAKE_VENVS'])
        elif sys.argv[1:] == ['environment', '--value', 'PIPX_BIN_DIR']:
            print(os.environ['FAKE_BIN_DIR'])
        else:
            assert sys.argv[1] in ['install', 'inject']
    """)
    executable(bin_dir / "tsb-tui", """
        #!/usr/bin/env python3
        import json, os, sys
        from pathlib import Path
        assert sys.argv[1:] == ['drive', '--help']
        engine = Path(os.environ['FAKE_VENVS']) / 'tsb-tui/lib/tsb-tui/libghostty-vt.so'
        assert engine.read_bytes() == b'engine bytes'
        with open(os.environ['CALL_LOG'], 'a') as log:
            log.write(json.dumps(['tsb-tui', *sys.argv[1:]]) + '\\n')
    """)
    for name in (TUI, CONTRACT):
        (assets / name).write_bytes(b'wheel test double')
    with tarfile.open(assets / ENGINE, "w:gz") as archive:
        for name, content in {"libghostty-vt.so": b"engine bytes", "PIN": b"pin",
                              "licenses/ghostty/LICENSE": b"license"}.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    shutil.copyfile(ROOT / "install.sh", assets / "install.sh")
    (assets / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256((assets / name).read_bytes()).hexdigest()}  {name}\n"
        for name in PAYLOADS if name != "install.ps1"
    ))
    return assets, venvs, log


def run_install(assets):
    return subprocess.run(["bash", "install.sh"], cwd=assets, capture_output=True, text=True,
                          check=False)


@pytest.mark.parametrize("name", [name for name in PAYLOADS if name != "install.ps1"])
def test_bad_checksum_stops_before_pipx(bundle, name):
    assets, venvs, log = bundle
    with (assets / name).open("ab") as output:
        output.write(b"\n# changed\n")
    result = run_install(assets)
    assert result.returncode != 0
    assert "FAILED" in result.stdout + result.stderr
    assert not log.exists()
    assert not venvs.exists()


def test_install_order_engine_location_and_write_boundaries(bundle):
    assets, venvs, log = bundle
    executable(venvs / "tsb-tui/bin/python", """
        #!/bin/bash
        exit 0
    """)
    before = {p: p.read_bytes() for p in assets.parent.rglob("*") if p.is_file()}
    # Unrelated manifest entries must not be checked or installed.
    with (assets / "SHA256SUMS").open("a") as manifest:
        manifest.write(f"{'0' * 64}  unrelated.whl\n")
    before[assets / "SHA256SUMS"] = (assets / "SHA256SUMS").read_bytes()
    result = run_install(assets)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [
        ["install", "--force", "--python", "python3.13", TUI],
        ["inject", "--force", "tsb-tui", CONTRACT],
        ["environment", "--value", "PIPX_LOCAL_VENVS"],
        ["environment", "--value", "PIPX_BIN_DIR"],
        ["tsb-tui", "drive", "--help"],
    ]
    engine = venvs / "tsb-tui/lib/tsb-tui/libghostty-vt.so"
    assert engine.read_bytes() == b"engine bytes"
    after = {p: p.read_bytes() for p in assets.parent.rglob("*") if p.is_file()}
    assert all(after[p] == content for p, content in before.items())
    assert after.keys() - before.keys() == {engine, log}
    assert "--broker" in result.stdout
    assert "tsb-tui" in result.stdout
    assert "pipx ensurepath" not in result.stdout


def test_install_succeeds_when_pipx_bin_is_missing_from_path(bundle, monkeypatch):
    assets, _venvs, log = bundle
    bin_dir = Path(os.environ["FAKE_BIN_DIR"])
    pipx_bin = assets.parent / "pipx bin"
    pipx_bin.mkdir()
    shutil.move(bin_dir / "tsb-tui", pipx_bin / "tsb-tui")
    executable(_venvs / "tsb-tui/bin/python", """
        #!/bin/bash
        exit 0
    """)
    monkeypatch.setenv("FAKE_BIN_DIR", str(pipx_bin))
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent), os.defpath])
    )
    assert str(pipx_bin) not in os.environ["PATH"].split(os.pathsep)
    result = run_install(assets)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"tsb-tui: {pipx_bin / 'tsb-tui'}" in result.stdout
    notices = [line for line in result.stdout.splitlines() if "pipx ensurepath" in line]
    assert len(notices) == 1
    assert str(pipx_bin) in notices[0]
    assert "not on PATH" in notices[0]
    assert json.loads(log.read_text().splitlines()[-1]) == ["tsb-tui", "drive", "--help"]
    assert list(Path(os.environ["HOME"]).iterdir()) == []


@pytest.mark.parametrize("version", ["glibc 2.28", "glibc 2.9", "musl 1.2.5", ""])
def test_unsupported_libc_stops_before_pipx(bundle, monkeypatch, version):
    assets, venvs, log = bundle
    monkeypatch.setenv("FAKE_GLIBC", version)
    result = run_install(assets)
    assert result.returncode != 0
    assert result.stderr.strip() == "tsb-tui requires glibc 2.29 or newer."
    assert not log.exists()
    assert not venvs.exists()


def test_non_x86_64_stops_before_the_engine(bundle, monkeypatch):
    assets, venvs, log = bundle
    bin_dir = Path(os.environ["FAKE_BIN_DIR"])
    executable(bin_dir / "uname", """
        #!/bin/bash
        printf 'aarch64\\n'
    """)
    result = run_install(assets)
    assert result.returncode == 1
    assert result.stderr.strip() == "tsb-tui's release ships the screen engine for x86_64 Linux only."
    assert not log.exists()
    assert not venvs.exists()


def test_python_older_than_3_12_stops_before_pipx(bundle, monkeypatch):
    assets, venvs, log = bundle
    bin_dir = Path(os.environ["FAKE_BIN_DIR"])
    for name in ("python3.13", "python3.12"):
        (bin_dir / name).unlink()
    fake_interpreter(bin_dir / "python3", False)
    system_path(bin_dir, monkeypatch)
    result = run_install(assets)
    assert result.returncode == 1
    assert result.stderr.strip() == "tsb-tui needs Python 3.12 or newer."
    assert not log.exists()
    assert not venvs.exists()


def test_engine_that_does_not_load_stops_before_drive(bundle):
    assets, venvs, log = bundle
    executable(venvs / "tsb-tui/bin/python", """
        #!/bin/bash
        exit 1
    """)
    result = run_install(assets)
    assert result.returncode == 1
    assert result.stderr.strip() == "The screen engine did not load; see the error above."
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert ["tsb-tui", "drive", "--help"] not in calls


@pytest.mark.parametrize("existing", ["yes", "no"])
def test_release_checksums_archive_and_both_upload_paths(tmp_path, monkeypatch, existing):
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    # Execute the packaging block verbatim; only wheel building is stubbed here.
    build = workflow.split("          rm -rf dist build tsb_tui.egg-info\n", 1)[1]
    build = "rm -rf dist build tsb_tui.egg-info\n" + textwrap.dedent(build.split("          BUILD\n", 1)[0])
    vendor = tmp_path / "vendor/libghostty-vt"
    shutil.copytree(ROOT / "vendor/libghostty-vt", vendor)
    shutil.copytree(ROOT / "tools", tmp_path / "tools")
    shutil.copytree(ROOT / "program/contract", tmp_path / "program/contract")
    shutil.copyfile(ROOT / "install.sh", tmp_path / "install.sh")
    shutil.copyfile(ROOT / "install.ps1", tmp_path / "install.ps1")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_EXISTING", existing)
    monkeypatch.setenv("RELEASE_TAG", "v1.2.3")
    monkeypatch.setenv("CALL_LOG", str(tmp_path / "calls.jsonl"))
    executable(bin_dir / "pip", f"""
        #!/usr/bin/env python3
        import sys
        from pathlib import Path
        args = sys.argv[1:]
        assert args[:3] == ['wheel', '--no-deps', '--no-build-isolation']
        destination = Path(args[args.index('-w') + 1])
        destination.mkdir(exist_ok=True)
        name = {TUI!r} if args[-1] == '.' else {CONTRACT!r}
        if name == {CONTRACT!r}:
            assert Path(args[-1]).resolve() != Path('program/contract').resolve()
            assert (Path(args[-1]) / 'pyproject.toml').is_file()
        (destination / name).write_bytes(b'wheel test double')
    """)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", build], cwd=tmp_path,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    dist = tmp_path / "dist"
    assert {p.name for p in dist.iterdir()} == set(PAYLOADS) | {"SHA256SUMS"}
    assert set((dist / "SHA256SUMS").read_text().splitlines()) == {
        f"{hashlib.sha256((dist / name).read_bytes()).hexdigest()}  {name}" for name in PAYLOADS
    }
    with tarfile.open(dist / ENGINE) as archive:
        assert set(archive.getnames()) == {"libghostty-vt.so", "PIN"} | {
            p.relative_to(vendor).as_posix() for p in (vendor / "licenses").rglob("*")
        } | {"licenses"}
        assert archive.extractfile("libghostty-vt.so").read() == (vendor / "libghostty-vt.so").read_bytes()
    runner_temp = tmp_path / "runner_temp"
    winzip = runner_temp / "winzip"
    winzip.mkdir(parents=True)
    zip_bytes = b"windows zip test double"
    (winzip / WINDOWS_ZIP).write_bytes(zip_bytes)
    zip_digest = hashlib.sha256(zip_bytes).hexdigest()
    zip_sha_line = f"{zip_digest}  {WINDOWS_ZIP}"
    (winzip / f"{WINDOWS_ZIP}.sha256").write_text(f"{zip_sha_line}\n")
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    earlier_sums = (dist / "SHA256SUMS").read_text().splitlines()

    stage = workflow.split("      - name: Stage the Windows zip into dist\n        run: |\n", 1)[1]
    stage = textwrap.dedent(stage.split("\n\n      - name: Publish", 1)[0])
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", stage], cwd=tmp_path,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (dist / WINDOWS_ZIP).is_file()
    assert (dist / f"{WINDOWS_ZIP}.sha256").is_file()
    assert {p.name for p in dist.iterdir()} == set(PAYLOADS) | {WINDOWS_ZIP, WINDOWS_ZIP + ".sha256", "SHA256SUMS"}
    assert (dist / "SHA256SUMS").read_text().splitlines() == earlier_sums + [
        zip_sha_line,
        f"{hashlib.sha256((dist / (WINDOWS_ZIP + '.sha256')).read_bytes()).hexdigest()}  {WINDOWS_ZIP}.sha256",
    ]
    executable(bin_dir / "gh", """
        #!/usr/bin/env python3
        import json, os, sys
        from pathlib import Path
        args = sys.argv[1:]
        if args[1] == 'view':
            if '--json' in args:
                print('\\n'.join(p.name for p in Path('dist').iterdir()))
            else:
                sys.exit(0 if os.environ['FAKE_EXISTING'] == 'yes' else 1)
        else:
            with open(os.environ['CALL_LOG'], 'a') as log:
                log.write(json.dumps(args) + '\\n')
    """)
    publish = "if gh release view" + workflow.split("          if gh release view", 1)[1]
    publish = textwrap.dedent("          " + publish)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", publish], cwd=tmp_path,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 1
    assert calls[0][1] == ("upload" if existing == "yes" else "create")
    assert {Path(arg).name for arg in calls[0] if arg.startswith("dist/")} == set(PAYLOADS) | {WINDOWS_ZIP, WINDOWS_ZIP + ".sha256", "SHA256SUMS"}
