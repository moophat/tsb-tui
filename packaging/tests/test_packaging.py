"""Packaging checks: run after a dummy-engine build, without running Windows code."""
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('assemble', HERE / 'assemble.py')
assemble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assemble)


@pytest.mark.parametrize('target', ['windows-x64', 'macos-arm64', 'macos-x64'])
def test_corrupt_cached_input(tmp_path, target):
    original = b'pinned input'
    digest = hashlib.sha256(original).hexdigest()
    lock = tmp_path / 'inputs.lock'
    lock.write_text(f'wheel {target} https://example.invalid/input {digest} {len(original)}\n')
    cached = tmp_path / digest
    cached.write_bytes(original)
    command = [str(HERE / 'verify-inputs.sh'), '--lock', str(lock), '--cache', str(tmp_path)]
    assert subprocess.run(command, capture_output=True, check=False).returncode == 0
    cached.write_bytes(b'P' + original[1:])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert 'inputs.lock:1' in result.stderr
    assert 'mismatch' in result.stderr


@pytest.mark.parametrize('count,marker', [(1, None), (2, 'main.dll\n'), (2, None)])
def test_engine_selection(tmp_path, count, marker):
    source = tmp_path / 'input'
    source.mkdir()
    (source / 'main.dll').write_bytes(b'dummy, not executable')
    if count == 2:
        (source / 'dependency.dll').write_bytes(b'dummy dependency')
    (source / 'licenses').mkdir()
    (source / 'licenses' / 'INDEX').write_text('Dummy engine for packaging tests only\n')
    if marker is not None:
        (source / 'ENGINE').write_text(marker)
    engine = tmp_path / 'output' / 'engine'
    licenses = tmp_path / 'output' / 'licenses'
    if count == 2 and marker is None:
        with pytest.raises(ValueError, match='more than one DLL; ENGINE file is required'):
            assemble.copy_engine(source, engine, licenses)
        assert not engine.exists()
    else:
        assemble.copy_engine(source, engine, licenses)
        assert (engine / 'ENGINE').read_text() == 'main.dll\n'
        assert len(list(engine.glob('*.dll'))) == count
        assert (licenses / 'INDEX').is_file()


def test_missing_distribution_license_fails(tmp_path):
    site = tmp_path / 'site'
    (site / 'unlicensed-1.dist-info').mkdir(parents=True)
    engine = tmp_path / 'engine'
    engine.mkdir()
    (engine / 'INDEX').write_text('Engine index')
    with pytest.raises(ValueError, match='unlicensed-1.dist-info'):
        assemble.notices(site, engine)


def leak_pattern():
    pattern = os.environ.get('TSB_LEAK_PATTERN', '')
    private = HERE.parent.parent / 'tsb-program/ops/publish/leak-pattern'
    if not pattern and private.is_file():
        pattern = private.read_text().split('\n', 1)[0].rstrip('\r')
    if not pattern:
        pytest.skip('TSB_LEAK_PATTERN and private tsb-program/ops/publish/leak-pattern are missing or empty')
    return re.compile(pattern, re.IGNORECASE)


@pytest.mark.parametrize('source', ['environment', 'file', 'missing'])
def test_leak_pattern_sources(tmp_path, monkeypatch, source):
    monkeypatch.setattr(sys.modules[__name__], 'HERE', tmp_path / 'consumer/packaging')
    monkeypatch.delenv('TSB_LEAK_PATTERN', raising=False)
    if source == 'environment':
        monkeypatch.setenv('TSB_LEAK_PATTERN', 'synthetic-environment-marker')
    if source in {'environment', 'file'}:
        private = tmp_path / 'tsb-program/ops/publish/leak-pattern'
        private.parent.mkdir(parents=True)
        private.write_text('synthetic-file-marker\nunused-push-marker\n')
        expected = 'synthetic-environment-marker' if source == 'environment' else 'synthetic-file-marker'
        assert leak_pattern().search(expected)
    else:
        with pytest.raises(pytest.skip.Exception, match='TSB_LEAK_PATTERN.*leak-pattern'):
            leak_pattern()


def assert_no_authored_leaks(archive, root):
    pattern = leak_pattern()
    files = {'tsb-tui', 'tsb-tui-check', 'tsb-tui.cmd', 'tsb-tui-check.cmd', 'README.txt', 'VERSION', 'lib/engine/ENGINE'}
    trees = ('lib/site/tsb_tui/', 'lib/site/tsb_contract_client/', 'lib/site/tsbc/')
    for name in archive.namelist():
        if not name.startswith(root) or name.endswith('/'):
            continue
        relative = name[len(root):]
        if relative in files or relative.startswith(trees):
            assert not pattern.search(archive.read(name).decode('utf-8', errors='replace')), name


def test_authored_leak_fails(tmp_path):
    archive = tmp_path / 'test.zip'
    root = 'tsb-tui-test-windows-x64/'
    # Construct an address the repository guard catches, without publishing one here.
    leak = '.'.join(map(str, (100, 64, 0, 1)))
    with zipfile.ZipFile(archive, 'w') as z:
        for name in ['python/Lib/ipaddress.py', 'lib/site/dependency/__init__.py',
                     'licenses/engine/INDEX']:
            z.writestr(root + name, leak)
    with zipfile.ZipFile(archive) as z:
        assert_no_authored_leaks(z, root)
    with zipfile.ZipFile(archive, 'a') as z:
        z.writestr(root + 'lib/site/tsb_tui/leaked.txt', leak)
    with zipfile.ZipFile(archive) as z, pytest.raises(AssertionError, match='tsb_tui/leaked.txt'):
        assert_no_authored_leaks(z, root)


def test_built_zip(tmp_path, monkeypatch):
    archives = sorted((HERE / 'out').glob('tsb-tui-*-windows-x64.zip'))
    if not archives:
        from test_macos_build import build_cached
        archives = [build_cached('windows-x64', tmp_path, monkeypatch)]
    for archive in archives:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            roots = {n.split('/')[0] for n in names}
            assert len(roots) == 1
            root = roots.pop() + '/'
            required = ['python/python.exe', 'lib/engine/ENGINE', 'licenses/engine/INDEX',
                        'lib/site/tsb_tui/__main__.py', 'lib/site/tsb_contract_client/__init__.py',
                        'lib/site/tsbc/__init__.py', 'THIRD_PARTY_NOTICES', 'README.txt', 'VERSION']
            assert all(root + name in names for name in required)
            main = z.read(root + 'lib/engine/ENGINE').decode().strip()
            assert root + 'lib/engine/' + main in names
            launcher = z.read(root + 'tsb-tui.exe')
            assert launcher[:2] == b'MZ'
            assert root + 'config.toml' not in names
            readme = z.read(root + 'README.txt').decode()
            assert 'Run: tsb-tui --broker' in readme
            assert 'tsb-tui.cmd' not in readme
            for name in ['tsb-tui.cmd', 'tsb-tui-check.cmd']:
                data = z.read(root + name)
                assert b'\r\n' in data
                assert b'\n' not in data.replace(b'\r\n', b'')
                assert b'lib\\engine\\ENGINE' in data
                assert b'%~dp0' in data
            assert not any('__pycache__' in n.split('/') or n.endswith('.pyc') for n in names)
            assert_no_authored_leaks(z, root)
            assert z.read(root + 'VERSION').decode().strip() in root
        checksum = archive.with_suffix('.zip.sha256').read_text().split()[0]
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == checksum


@pytest.mark.parametrize('relative', ['main.dll', 'licenses/NOTICE', 'ENGINE', 'licenses/linked-dir'])
def test_engine_symlinks_rejected(tmp_path, relative):
    source = tmp_path / 'input'
    (source / 'licenses').mkdir(parents=True)
    (source / 'main.dll').write_bytes(b'dummy DLL')
    (source / 'licenses' / 'INDEX').write_text('Engine licence index')
    target = tmp_path / 'external'
    target.write_text('external contents')
    link = source / relative
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    with pytest.raises(ValueError) as error:
        assemble.copy_engine(source, tmp_path / 'output/engine', tmp_path / 'output/licenses')
    assert str(link) in str(error.value)
    assert not (tmp_path / 'output').exists()


@pytest.fixture
def assembly_inputs(tmp_path, monkeypatch):
    packaging = tmp_path / 'packaging'
    (packaging / 'cache').mkdir(parents=True)
    (packaging / 'launchers').mkdir()
    for name in ['tsb-tui.cmd', 'tsb-tui-check.cmd']:
        (packaging / 'launchers' / name).write_text('launcher')
    (packaging / 'cache' / 'tsb-tui.exe').write_bytes(b'MZ dummy launcher')
    runtime = tmp_path / 'python'
    runtime.mkdir()
    (runtime / 'python.exe').write_bytes(b'dummy runtime')
    with tarfile.open(packaging / 'cache/runtime', 'w') as archive:
        archive.add(runtime, arcname='python')
    engine = tmp_path / 'engine'
    (engine / 'licenses').mkdir(parents=True)
    (engine / 'main.dll').write_bytes(b'dummy DLL')
    (engine / 'licenses/INDEX').write_text('Engine licence index')
    root = tmp_path / 'empty-repo'
    root.mkdir()
    monkeypatch.setattr(assemble, 'ROOT', root)
    monkeypatch.setattr(assemble, 'HERE', packaging)
    copytree = assemble.shutil.copytree
    repository_trees = {
        root / 'tsb_tui',
        root / 'contract/client/tsb_contract_client',
        root / 'contract/tsbc',
    }

    def copy_repository_tree(source, destination, **kwargs):
        if source in repository_trees:
            Path(destination).mkdir(parents=True)
            return destination
        return copytree(source, destination, **kwargs)

    read_text = Path.read_text

    def read_project(path, *args, **kwargs):
        if path == root / 'pyproject.toml':
            return '[project]\nversion = "0.0.0"\n'
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(assemble.shutil, 'copytree', copy_repository_tree)
    monkeypatch.setattr(Path, 'read_text', read_project)
    monkeypatch.setattr(assemble, 'verify', lambda *args, **kwargs: [
        ('windows-x64', 'python', 'unused', 'runtime', 0)])
    def git_output(command, **kwargs):
        if command in [
            ['git', 'status', '--porcelain', '--untracked-files=no'],
            ['git', 'ls-files', '--others', '--', 'tsb_tui', ':(exclude)**/__pycache__/**', ':(exclude)**/*.pyc'],
        ]:
            return ''
        assert command == ['git', 'describe', '--tags', '--exact-match', 'HEAD']
        return 'v1.2.3'

    monkeypatch.setattr(assemble.subprocess, 'check_output', git_output)
    monkeypatch.setenv('SOURCE_DATE_EPOCH', '1700000000')

    def run(*args, **kwargs):
        if '--target' in args:
            Path(args[args.index('--target') + 1]).mkdir(parents=True, exist_ok=True)
        if args[0] == 'zip':
            Path(args[4]).write_bytes(b'archive would be written')

    monkeypatch.setattr(assemble, 'run', run)
    return packaging, engine


@pytest.mark.parametrize('control', ['\n', '\r', '\t', '\x01', '\x1f', '\x7f'])
def test_control_character_path_rejected(assembly_inputs, control):
    packaging, engine = assembly_inputs
    name = 'NOTICE' + control + 'unexpected'
    (engine / 'licenses' / name).write_text('licence contents')
    with pytest.raises(ValueError) as error:
        assemble.assemble(engine)
    relative = f'tsb-tui-1.2.3-windows-x64/licenses/engine/{name}'
    assert repr(relative) in str(error.value)
    assert not list((packaging / 'out').glob('*.zip'))
    assert list(assemble.ROOT.iterdir()) == []


def test_contract_pin_failure_precedes_inputs(tmp_path, monkeypatch, capsys):
    packaging = tmp_path / 'packaging'
    monkeypatch.setattr(assemble, 'HERE', packaging)
    calls = []

    def fail_check(command, **kwargs):
        calls.append(command)
        assert command == ['bash', 'tools/contract-pin.sh', 'copy-check']
        assert kwargs['cwd'] == assemble.ROOT
        assert kwargs['check'] is True
        print('contract-check: deliberate failure', file=sys.stderr)
        raise subprocess.CalledProcessError(1, command)

    def refuse_inputs(*args, **kwargs):
        pytest.fail('input verification ran before contract-pin check')

    monkeypatch.setattr(assemble.subprocess, 'run', fail_check)
    monkeypatch.setattr(assemble, 'verify', refuse_inputs)
    with pytest.raises(subprocess.CalledProcessError):
        assemble.assemble(tmp_path / 'engine')
    assert len(calls) == 1
    assert 'contract-check: deliberate failure' in capsys.readouterr().err
    assert not packaging.exists()


@pytest.mark.parametrize('tracked,untracked,message', [
    (' M tsb_tui/app.py\n', '', 'tracked changes:  M tsb_tui/app.py'),
    ('', 'tsb_tui/extra.py\n', 'untracked files under tsb_tui: tsb_tui/extra.py'),
    ('', 'tsb_tui/local_settings.json\n', 'untracked files under tsb_tui: tsb_tui/local_settings.json'),
])
def test_dirty_source_precedes_inputs(tmp_path, monkeypatch, tracked, untracked, message):
    root = tmp_path / 'repo'
    root.mkdir()
    monkeypatch.setattr(assemble, 'ROOT', root)
    monkeypatch.setattr(assemble, 'HERE', root / 'packaging')
    calls = []

    def run(*args, **kwargs):
        assert args == ('bash', 'tools/contract-pin.sh', 'copy-check')
        assert kwargs['cwd'] == root
        calls.append('contract')

    def git_output(command, **kwargs):
        assert calls[0] == 'contract'
        assert kwargs == {'cwd': root, 'text': True}
        if command == ['git', 'status', '--porcelain', '--untracked-files=no']:
            return tracked
        assert command == ['git', 'ls-files', '--others', '--', 'tsb_tui', ':(exclude)**/__pycache__/**', ':(exclude)**/*.pyc']
        return untracked

    def refuse_inputs(*args, **kwargs):
        pytest.fail('input verification ran before source cleanliness check')

    monkeypatch.setattr(assemble, 'run', run)
    monkeypatch.setattr(assemble.subprocess, 'check_output', git_output)
    monkeypatch.setattr(assemble, 'verify', refuse_inputs)
    with pytest.raises(ValueError) as error:
        assemble.assemble(tmp_path / 'engine')
    assert message in str(error.value)
    assert list(root.iterdir()) == []


def test_launcher_sets_portable_config_home():
    for name in ('tsb-tui.cmd', 'tsb-tui-check.cmd'):
        launcher = (HERE / 'launchers' / name).read_text()
        assert 'set "TSB_TUI_HOME=%~dp0"' in launcher, name


def test_missing_launcher_fails_before_the_zip(assembly_inputs):
    packaging, engine = assembly_inputs
    (packaging / 'cache' / 'tsb-tui.exe').unlink()
    with pytest.raises(ValueError, match='build-launcher.sh'):
        assemble.assemble(engine)
    assert not list((packaging / 'out').glob('*.zip'))


def test_assembled_bundle_holds_the_exe_and_never_a_config(assembly_inputs, monkeypatch):
    _packaging, engine = assembly_inputs
    seen = {}
    run = assemble.run

    def capture(*args, **kwargs):
        if args[0] == 'zip':
            bundle = Path(args[4]).parent / 'tsb-tui-1.2.3-windows-x64'
            seen['files'] = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
            seen['exe'] = (bundle / 'tsb-tui.exe').read_bytes()
            seen['readme'] = (bundle / 'README.txt').read_text()
        return run(*args, **kwargs)

    monkeypatch.setattr(assemble, 'run', capture)
    assemble.assemble(engine)
    assert {'tsb-tui.exe', 'tsb-tui-check.cmd'} <= seen['files']
    assert 'config.toml' not in seen['files']
    assert seen['exe'] == b'MZ dummy launcher'
    assert 'Run: tsb-tui --broker' in seen['readme']


def test_launcher_sources_carry_no_leaks():
    pattern = leak_pattern()
    for path in sorted((HERE / 'launchers').iterdir()):
        assert not pattern.search(path.read_text()), path.name
