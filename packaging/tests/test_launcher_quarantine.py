"""The macOS/Linux shell launchers clear macOS's download quarantine from their own folder before Python starts."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

LAUNCHERS = Path(__file__).resolve().parents[1] / 'launchers'
NAMES = ['tsb-tui', 'tsb-tui-check']


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def bundle(tmp_path, name, system, xattr_exit=0):
    """An unzipped folder with a fake bundled Python, plus stubbed uname and xattr on PATH."""
    folder = tmp_path / 'unzipped folder'
    log = tmp_path / 'calls.log'
    folder.mkdir()
    shutil.copy(LAUNCHERS / name, folder / name)
    executable(folder / 'python' / 'bin' / 'python3',
               f'#!/bin/sh\necho python >> "{log}"\n')
    stubs = tmp_path / 'stubs'
    executable(stubs / 'uname', f'#!/bin/sh\necho {system}\n')
    if xattr_exit is not None:
        executable(stubs / 'xattr', f'#!/bin/sh\necho "xattr $*" >> "{log}"\nexit {xattr_exit}\n')
    return folder, stubs, log


def run(folder, stubs, name):
    env = {'PATH': f'{stubs}:/usr/bin:/bin', 'HOME': str(folder)}
    return subprocess.run(['/bin/sh', str(folder / name)], env=env, capture_output=True, text=True)


@pytest.mark.parametrize('name', NAMES)
def test_darwin_clears_quarantine_of_its_folder_before_python(tmp_path, name):
    folder, stubs, log = bundle(tmp_path, name, 'Darwin')
    result = run(folder, stubs, name)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        f'xattr -dr com.apple.quarantine {folder.resolve()}', 'python']
    assert result.stdout == '' and result.stderr == ''


@pytest.mark.parametrize('name', NAMES)
def test_linux_never_calls_xattr(tmp_path, name):
    folder, stubs, log = bundle(tmp_path, name, 'Linux')
    result = run(folder, stubs, name)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == ['python']


@pytest.mark.parametrize('name', NAMES)
def test_failing_xattr_does_not_stop_the_launch(tmp_path, name):
    folder, stubs, log = bundle(tmp_path, name, 'Darwin', xattr_exit=1)
    result = run(folder, stubs, name)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[-1] == 'python'
    assert result.stdout == '' and result.stderr == ''


@pytest.mark.parametrize('name', NAMES)
def test_darwin_without_xattr_still_launches(tmp_path, name):
    folder, stubs, log = bundle(tmp_path, name, 'Darwin', xattr_exit=None)
    result = run(folder, stubs, name)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == ['python']
