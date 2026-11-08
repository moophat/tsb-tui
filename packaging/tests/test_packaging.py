"""Packaging checks: run after a dummy-engine build, without running Windows code."""
import hashlib
import importlib.util
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('assemble', HERE / 'assemble.py')
assemble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assemble)


def test_corrupt_cached_input(tmp_path):
    original = b'pinned input'
    digest = hashlib.sha256(original).hexdigest()
    lock = tmp_path / 'inputs.lock'
    lock.write_text(f'wheel windows-x64 https://example.invalid/input {digest} {len(original)}\n')
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


def assert_no_authored_leaks(archive, root):
    # Read the repository guard instead of maintaining a second leak pattern.
    guard = (HERE.parent / '.github/scripts/leak-check.sh').read_text()
    pattern = re.compile(re.search(r"^PATTERN='(.*)'$", guard, re.MULTILINE).group(1), re.IGNORECASE)
    files = {'tsb-tui.cmd', 'tsb-tui-check.cmd', 'README.txt', 'VERSION', 'lib/engine/ENGINE'}
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


def test_built_zip():
    archives = sorted((HERE / 'out').glob('tsb-tui-*-windows-x64.zip'))
    assert archives, 'Run the full dummy-engine build before these tests'
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
