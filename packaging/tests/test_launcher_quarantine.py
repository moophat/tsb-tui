"""The macOS/Linux shell launchers clear macOS's download quarantine from their own bundle before Python starts."""
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


def bundle(tmp_path, name, system, xattr_exit=0, quarantined=True, is_bundle=True):
    """An unzipped folder with a fake bundled Python, plus stubbed uname and xattr on PATH.

    quarantined: whether `xattr -p` finds the flag on the bundled Python.
    is_bundle: whether the folder has the library folder a real bundle has.
    """
    folder = tmp_path / 'unzipped folder'
    log = tmp_path / 'calls.log'
    folder.mkdir()
    shutil.copy(LAUNCHERS / name, folder / name)
    executable(folder / 'python' / 'bin' / 'python3',
               f'#!/bin/sh\necho python >> "{log}"\n')
    if is_bundle:
        (folder / 'lib' / 'site').mkdir(parents=True)
    stubs = tmp_path / 'stubs'
    executable(stubs / 'uname', f'#!/bin/sh\necho {system}\n')
    if xattr_exit is not None:
        probe_exit = 0 if quarantined else 1
        executable(stubs / 'xattr',
                   f'#!/bin/sh\necho "xattr $*" >> "{log}"\n'
                   f'if [ "$1" = -p ]; then exit {probe_exit}; fi\nexit {xattr_exit}\n')
    return folder, stubs, log


def run(folder, stubs, name):
    env = {'PATH': f'{stubs}:/usr/bin:/bin', 'HOME': str(folder)}
    return subprocess.run(['/bin/sh', str(folder / name)], env=env, capture_output=True, text=True)


@pytest.mark.parametrize('name', NAMES)
def test_darwin_clears_quarantine_of_the_bundle_paths_before_python(tmp_path, name):
    folder, stubs, log = bundle(tmp_path, name, 'Darwin')
    result = run(folder, stubs, name)
    assert result.returncode == 0, result.stderr
    here = folder.resolve()
    assert log.read_text().splitlines() == [
        f'xattr -p com.apple.quarantine {here}/python/bin/python3',
        f'xattr -dr com.apple.quarantine {here}/lib {here}/tsb-tui {here}/tsb-tui-check {here}/python',
        'python']
    assert result.stdout == '' and result.stderr == ''


@pytest.mark.parametrize('name', NAMES)
def test_a_launcher_outside_a_bundle_leaves_its_folder_alone(tmp_path, name):
    folder, stubs, log = bundle(tmp_path, name, 'Darwin', is_bundle=False)
    result = run(folder, stubs, name)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == ['python']


@pytest.mark.parametrize('name', NAMES)
def test_an_already_cleared_bundle_is_not_walked_again(tmp_path, name):
    folder, stubs, log = bundle(tmp_path, name, 'Darwin', quarantined=False)
    result = run(folder, stubs, name)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        f'xattr -p com.apple.quarantine {folder.resolve()}/python/bin/python3', 'python']


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
