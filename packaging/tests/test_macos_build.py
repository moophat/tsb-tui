"""End-to-end macOS assembly using only already-cached locked inputs."""
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(HERE))
import assemble
from inputs import verify


def build_cached(target, directory, monkeypatch):
    """Run real assembly, answering only the clean-status and exact-tag queries."""
    from inputs import entries
    for number, _, url, digest, _ in entries(HERE / 'inputs.lock', target):
        if not (HERE / 'cache' / digest).is_file():
            pytest.skip(f'inputs.lock:{number}: missing {url.rsplit("/", 1)[-1]}')
    verify(HERE / 'inputs.lock', HERE / 'cache', fetch=False, target=target)
    if target == 'windows-x64' and not (HERE / 'cache/tsb-tui.exe').is_file():
        pytest.skip('missing packaging/cache/tsb-tui.exe; run packaging/build-launcher.sh')
    original = assemble.subprocess.check_output

    def checked(command, **kwargs):
        if command == ['git', 'status', '--porcelain', '--untracked-files=no']:
            return ''
        if command == ['git', 'describe', '--tags', '--exact-match', 'HEAD']:
            return 'v0.0.0'
        return original(command, **kwargs)

    for name in ['inputs.lock', 'cache', 'engine', 'launchers', f'requirements-{target}.txt']:
        (directory / name).symlink_to(HERE / name)
    with monkeypatch.context() as patch:
        patch.setattr(assemble.subprocess, 'check_output', checked)
        patch.setattr(assemble, 'HERE', directory)
        assemble.assemble(HERE / 'engine/windows-x64' if target == 'windows-x64' else None, target)
    return directory / 'out' / f'tsb-tui-0.0.0-{target}.zip'


@pytest.mark.parametrize('target', ['macos-arm64', 'macos-x64'])
def test_macos_zip_end_to_end(target, monkeypatch, tmp_path):
    archive = build_cached(target, tmp_path, monkeypatch)
    check_zip(archive, target)
    print(f'{target}: {archive.stat().st_size} bytes; ' + archive.with_suffix('.zip.sha256').read_text())


def check_zip(archive, target, scratch=None):
    import hashlib
    import struct
    import zipfile

    from test_packaging import assert_no_authored_leaks
    with zipfile.ZipFile(archive) as z:
        root = archive.stem + '/'
        names = z.namelist()
        required = ['python/bin/python3', 'python/bin/python3.12', 'tsb-tui', 'tsb-tui-check',
                    'lib/engine/ENGINE', 'lib/engine/libghostty-vt.dylib', 'licenses/engine/INDEX',
                    'lib/site/tsb_tui/__main__.py', 'lib/site/tsb_contract_client/__init__.py',
                    'lib/site/tsbc/__init__.py', 'README.txt', 'VERSION', 'THIRD_PARTY_NOTICES']
        assert all(root + name in names for name in required)
        assert names == sorted(names)
        assert not any('__pycache__' in n.split('/') or n.endswith('.pyc') for n in names)
        assert z.read(root + 'lib/engine/ENGINE') == b'libghostty-vt.dylib\n'
        readme = z.read(root + 'README.txt').decode()
        assert 'Run: ./tsb-tui --broker https://<host>' in readme
        assert 'Check the engine and TUI versions: ./tsb-tui-check' in readme
        for name in ['tsb-tui', 'tsb-tui-check', 'python/bin/python3', 'python/bin/python3.12']:
            assert (z.getinfo(root + name).external_attr >> 16) & 0o777 == 0o755
            info = subprocess.check_output(['zipinfo', '-l', str(archive), root + name], text=True)
            assert '-rwxr-xr-x' in info
        for name in ['tsb-tui', 'tsb-tui-check']:
            assert b'\r' not in z.read(root + name)
        assert_no_authored_leaks(z, root)
        cpu = struct.unpack_from('<I', z.read(root + 'python/bin/python3.12'), 4)[0]
        assert cpu == {'macos-arm64': 0x100000c, 'macos-x64': 0x1000007}[target]
        print(f'{target} python/bin/python3.12 cputype: {cpu:#010x}')
        for name in ['python3', 'python3.12']:
            interpreter = (scratch or archive.parent) / (target + '-' + name)
            interpreter.write_bytes(z.read(root + 'python/bin/' + name))
            subprocess.run([sys.executable, '-I', str(HERE / 'engine/check-macho.py'),
                            str(interpreter), 'arm64' if target == 'macos-arm64' else 'x86_64',
                            '--executable'], check=True)
        engine = (scratch or archive.parent) / (target + '.dylib')
        engine.write_bytes(z.read(root + 'lib/engine/libghostty-vt.dylib'))
        subprocess.run([sys.executable, '-I', str(HERE / 'engine/check-macho.py'), str(engine),
                        'arm64' if target == 'macos-arm64' else 'x86_64'], check=True)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == archive.with_suffix('.zip.sha256').read_text().split()[0]


@pytest.mark.parametrize('launcher', ['tsb-tui', 'tsb-tui-check'])
@pytest.mark.parametrize('launch', ['direct', 'symlink', 'spaces', 'shell', 'shell-decoy'])
def test_posix_launcher(tmp_path, launcher, launch):
    import os
    import shutil
    folder = tmp_path / ('folder with spaces' if launch == 'spaces' else 'bundle')
    (folder / 'python/bin').mkdir(parents=True)
    for name in ['tsb-tui', 'tsb-tui-check']:
        shutil.copy2(HERE / 'launchers' / name, folder / name)
    stub = folder / 'python/bin/python3'
    stub.write_text('#!/bin/sh\nprintf "%s\\0" "$PYTHONNOUSERSITE" "$PYTHONPATH" "$TSB_TUI_TERM_LIBRARY" "$TSB_TUI_HOME" "$PATH" "$@"\n')
    stub.chmod(0o755)
    entry = folder / launcher
    if launch == 'symlink':
        links = tmp_path / 'links'
        links.mkdir()
        # Include a relative hop and an absolute hop.
        (links / 'hop').symlink_to(entry)
        entry = links / launcher
        entry.symlink_to('hop')
    arguments = ['a b', '', '"quoted"', '$literal', 'a\nb', '*', '--flag']
    env = {**os.environ, 'PYTHONNOUSERSITE': 'wrong', 'PYTHONPATH': 'wrong',
           'TSB_TUI_HOME': 'wrong', 'TSB_TUI_TERM_LIBRARY': 'wrong'}
    command = [str(entry), *arguments]
    cwd = tmp_path
    if launch in {'shell', 'shell-decoy'}:
        cwd = folder
        command = ['sh', launcher, *arguments]
        if launch == 'shell-decoy':
            decoy = tmp_path / 'decoy'
            decoy.mkdir()
            (decoy / launcher).write_text('#!/bin/sh\nexit 99\n')
            (decoy / launcher).chmod(0o755)
            env['PATH'] = str(decoy) + ':' + env['PATH']
    result = subprocess.run(command, env=env, cwd=cwd, check=True, capture_output=True)
    values = result.stdout.decode().split('\0')[:-1]
    assert values[:5] == ['1', str(folder / 'lib/site'), str(folder / 'lib/engine/libghostty-vt.dylib'),
                          str(folder), str(folder / 'lib/engine') + ':' + env['PATH']]
    assert values[-len(arguments):] == arguments
    if launcher == 'tsb-tui':
        assert values[5:7] == ['-m', 'tsb_tui']
    else:
        assert values[5] == '-c'
        command = (HERE / 'launchers/tsb-tui-check.cmd').read_text().split(' -c "', 1)[1].split('"', 1)[0]
        assert values[6] == command

@pytest.mark.parametrize('launcher', ['tsb-tui', 'tsb-tui-check'])
def test_posix_launcher_bare_name_from_bundle(tmp_path, launcher):
    import shutil
    folder = tmp_path / 'bundle'
    (folder / 'python/bin').mkdir(parents=True)
    for name in ['tsb-tui', 'tsb-tui-check']:
        shutil.copy2(HERE / 'launchers' / name, folder / name)
    stub = folder / 'python/bin/python3'
    stub.write_text('#!/bin/sh\necho invoked\n')
    stub.chmod(0o755)
    result = subprocess.run(['sh', launcher], cwd=folder, env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

@pytest.mark.parametrize('launcher', ['tsb-tui', 'tsb-tui-check'])
def test_posix_launcher_missing_name_explains_run_command(tmp_path, launcher):
    result = subprocess.run(['sh', '-c', (HERE / 'launchers' / launcher).read_text(), launcher], cwd=tmp_path, env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
    assert result.returncode != 0
    assert f'./{launcher} from the unzipped folder.' in result.stderr


@pytest.mark.parametrize('target', ['macos-arm64', 'macos-x64'])
def test_macos_readme_commands(target, tmp_path, monkeypatch):
    import zipfile
    archive = build_cached(target, tmp_path, monkeypatch)
    with zipfile.ZipFile(archive) as z:
        readme = z.read(archive.stem + '/README.txt').decode()
    assert 'Run: ./tsb-tui --broker https://<host>' in readme
    assert 'Check the engine and TUI versions: ./tsb-tui-check' in readme

@pytest.mark.parametrize('target', ['macos-arm64', 'macos-x64'])
def test_staged_macos_zips(target, tmp_path):
    import zipfile
    archives = sorted((HERE / 'out').glob(f'tsb-tui-*-{target}.zip'))
    assert archives, f'missing staged {target} zip'
    for archive in archives:
        with zipfile.ZipFile(archive) as z:
            readme = z.read(archive.stem + '/README.txt').decode()
        assert 'Run: ./tsb-tui --broker https://<host>' in readme
        assert 'Check the engine and TUI versions: ./tsb-tui-check' in readme
        check_zip(archive, target, tmp_path)

@pytest.mark.parametrize('arch,cpu', [('arm64', 0x100000c), ('x86_64', 0x1000007)])
def test_macho_interpreter_architecture(tmp_path, arch, cpu):
    import struct
    interpreter = tmp_path / 'python'
    interpreter.write_bytes(struct.pack('<IIIIIIII', 0xfeedfacf, cpu, 0 if arch == 'arm64' else 3, 2, 0, 0, 0, 0))
    command = [sys.executable, str(HERE / 'engine/check-macho.py'), str(interpreter), arch, '--executable']
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    wrong = 'x86_64' if arch == 'arm64' else 'arm64'
    result = subprocess.run([*command[:3], wrong, '--executable'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'cputype' in result.stderr


def test_staged_macos_zip_missing_readme_commands_fails(tmp_path, monkeypatch):
    import zipfile
    out = tmp_path / 'out'
    out.mkdir()
    archive = out / 'tsb-tui-0.0.0-macos-arm64.zip'
    monkeypatch.setattr(sys.modules[__name__], 'HERE', tmp_path)
    monkeypatch.setattr(sys.modules[__name__], 'check_zip', lambda *args, **kwargs: None)
    for missing in [
        'plain text',
        'Run: ./tsb-tui --broker https://<host>',
        'Check the engine and TUI versions: ./tsb-tui-check',
    ]:
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('tsb-tui-0.0.0-macos-arm64/README.txt', missing + '\n')
        with pytest.raises(AssertionError):
            test_staged_macos_zips('macos-arm64', tmp_path)
    calls = []
    monkeypatch.setattr(sys.modules[__name__], 'check_zip', lambda *args, **kwargs: calls.append(args))
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('tsb-tui-0.0.0-macos-arm64/README.txt',
                   'Run: ./tsb-tui --broker https://<host>\n'
                   'Check the engine and TUI versions: ./tsb-tui-check\n')
    test_staged_macos_zips('macos-arm64', tmp_path)
    assert len(calls) == 1

