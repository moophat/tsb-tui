"""Assemble the Windows x64 distribution without executing target code."""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.parse
from pathlib import Path

from inputs import HERE, verify

ROOT = HERE.parent


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def copy_engine(source, destination, licenses):
    """Accept a directory or tarball with DLLs, licenses/INDEX and optional ENGINE."""
    for path in [source, *source.rglob('*')]:
        if path.is_symlink():
            raise ValueError(f'engine input contains a symlink: {path}')
    dlls = sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() == '.dll')
    if not dlls:
        raise ValueError('engine input contains no DLL')
    names = [p.name for p in dlls]
    if len({n.lower() for n in names}) != len(names):
        raise ValueError('engine input contains duplicate DLL file names')
    marker = source / 'ENGINE'
    if len(dlls) > 1:
        if not marker.is_file():
            raise ValueError('engine input contains more than one DLL; ENGINE file is required')
        lines = marker.read_text().splitlines()
        if len(lines) != 1 or lines[0] not in names:
            raise ValueError('ENGINE must contain one line naming a bundled DLL')
        main = lines[0]
    else:
        main = dlls[0].name
    if not (source / 'licenses' / 'INDEX').is_file():
        raise ValueError('engine input is missing licenses/INDEX')
    destination.mkdir(parents=True)
    for dll in dlls:
        shutil.copyfile(dll, destination / dll.name)
    (destination / 'ENGINE').write_text(main + '\n')
    shutil.copytree(source / 'licenses', licenses)


def notices(site, engine):
    parts = ['Engine licence index\n' + (engine / 'INDEX').read_text()]
    for info in sorted(site.glob('*.dist-info')):
        # TSB is first-party; its licensing decision is outside this track.
        if info.name.startswith('tsb_tui-'):
            continue
        files = sorted(p for p in info.rglob('*') if p.is_file() and (
            'licenses' in p.relative_to(info).parts or
            p.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE'))))
        if not files:
            raise ValueError(f'bundled distribution has no licence file: {info.name}')
        parts.append('\n' + info.name + '\n')
        for path in files:
            parts.append(path.relative_to(info).as_posix() + '\n' + path.read_text(encoding='utf-8'))
    return '\n'.join(parts)


def assemble(engine):
    run('bash', 'tools/contract-pin.sh', 'check', cwd=ROOT)
    tracked = subprocess.check_output(
        ['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, text=True)
    if tracked:
        raise ValueError(f'tracked changes: {tracked.rstrip()}')
    untracked = subprocess.check_output(
        ['git', 'ls-files', '--others', '--exclude-standard', '--', 'tsb_tui'], cwd=ROOT, text=True)
    if untracked:
        raise ValueError(f'untracked files under tsb_tui: {untracked.rstrip()}')
    rows = verify(HERE / 'inputs.lock', HERE / 'cache', fetch=True)
    runtimes = [row for row in rows if row[1] == 'python']
    if len(runtimes) != 1:
        raise ValueError('inputs.lock must name exactly one Python runtime')
    # Follow the release workflow's tag stamping, in a disposable source copy.
    tag = subprocess.check_output(['git', 'describe', '--tags', '--exact-match', 'HEAD'], cwd=ROOT, text=True).strip()
    if not tag.startswith('v'):
        raise ValueError('HEAD must have a version tag beginning with v')
    version = tag[1:]
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH') or subprocess.check_output(
        ['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT, text=True).strip())
    if epoch < 315532800:
        raise ValueError('SOURCE_DATE_EPOCH must be in the ZIP date range (1980 or later)')
    out = HERE / 'out'
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='assemble-', dir=HERE / 'cache') as temp:
        work = Path(temp)
        bundle = work / f'tsb-tui-{version}-windows-x64'
        bundle.mkdir()
        with tarfile.open(HERE / 'cache' / runtimes[0][3]) as archive:
            archive.extractall(bundle, filter='data')
        if not (bundle / 'python' / 'python.exe').is_file():
            raise ValueError('Python archive does not contain python/python.exe')
        wheels = work / 'wheels'
        wheels.mkdir()
        for _, kind, url, digest, _ in rows:
            if kind == 'wheel':
                name = urllib.parse.unquote(url.rsplit('/', 1)[-1])
                shutil.copyfile(HERE / 'cache' / digest, wheels / name)
        # Offline hash-required download also proves the pinned dependency closure.
        run(sys.executable, '-m', 'pip', 'download', '--no-index', '--find-links', wheels,
            '--dest', work / 'checked-wheels', '--platform', 'win_amd64', '--python-version', '3.12',
            '--implementation', 'cp', '--abi', 'cp312', '--only-binary=:all:', '--require-hashes',
            '-r', HERE / 'requirements-windows-x64.txt')
        site = bundle / 'lib' / 'site'
        install = [sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-index', '--no-compile',
                   '--find-links', wheels, '--target', site, '--platform', 'win_amd64',
                   '--python-version', '3.12', '--implementation', 'cp', '--abi', 'cp312', '--only-binary=:all:']
        run(*install, '--require-hashes', '-r', HERE / 'requirements-windows-x64.txt')
        source = work / 'source'
        source.mkdir()
        shutil.copytree(ROOT / 'tsb_tui', source / 'tsb_tui', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        project = (ROOT / 'pyproject.toml').read_text()
        if tomllib.loads(project)['project']['version'] != '0.0.0':
            raise ValueError('release source must declare version 0.0.0')
        (source / 'pyproject.toml').write_text(project.replace('version = "0.0.0"', f'version = "{version}"', 1))
        build_wheels = work / 'build-wheels'
        build_wheels.mkdir()
        for _, kind, url, digest, _ in rows:
            if kind == 'build':
                shutil.copyfile(HERE / 'cache' / digest,
                                build_wheels / urllib.parse.unquote(url.rsplit('/', 1)[-1]))
        host_tools = work / 'host-tools'
        run(sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-index', '--no-compile',
            '--target', host_tools, *sorted(build_wheels.glob('*.whl')))
        run(sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
            '--wheel-dir', wheels, source,
            env={**os.environ, 'PYTHONPATH': str(host_tools), 'PIP_NO_INDEX': '1'})
        run(*install, f'tsb-tui=={version}')
        # Linux pip emits host shebangs; the portable launchers replace these entry points.
        shutil.rmtree(site / 'bin', ignore_errors=True)
        for name, relative in [('tsb_contract_client', 'client/tsb_contract_client'), ('tsbc', 'tsbc')]:
            shutil.copytree(ROOT / 'program' / 'contract' / relative, site / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        if engine.is_dir():
            engine_dir = engine
        else:
            engine_dir = work / 'engine-input'
            engine_dir.mkdir()
            with tarfile.open(engine) as archive:
                archive.extractall(engine_dir, filter='data')
        copy_engine(engine_dir, bundle / 'lib' / 'engine', bundle / 'licenses' / 'engine')
        (bundle / 'THIRD_PARTY_NOTICES').write_text(notices(site, bundle / 'licenses' / 'engine'))
        for name in ['tsb-tui.cmd', 'tsb-tui-check.cmd']:
            shutil.copyfile(HERE / 'launchers' / name, bundle / name)
        (bundle / 'VERSION').write_text(version + '\n')
        (bundle / 'README.txt').write_text(
            'Extract this entire folder, then open Windows Terminal here.\n'
            'Run: tsb-tui.cmd --broker https://<host>\n'
            'Check the engine and TUI versions: tsb-tui-check.cmd\n')
        for path in list(bundle.rglob('__pycache__')):
            shutil.rmtree(path)
        for path in bundle.rglob('*.pyc'):
            path.unlink()
        for path in [bundle, *bundle.rglob('*')]:
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() else 0o644)
            os.utime(path, (epoch, epoch), follow_symlinks=False)
        archive_path = work / (bundle.name + '.zip')
        files = sorted(p.relative_to(work).as_posix() for p in bundle.rglob('*') if p.is_file())
        for path in files:
            if any(ord(char) < 32 or ord(char) == 127 for char in path):
                raise ValueError(f'archive path contains a control character: {path!r}')
        run('zip', '-X', '-r', '-q', archive_path, '-@', input='\n'.join(files) + '\n', text=True,
            cwd=work, env={**os.environ, 'TZ': 'UTC'})
        final = out / archive_path.name
        shutil.move(archive_path, final)
        digest = hashlib.sha256(final.read_bytes()).hexdigest()
        final.with_suffix('.zip.sha256').write_text(f'{digest}  {final.name}\n')
        print(f'Built {final.name}: {final.stat().st_size} bytes; sha256 {digest}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=['windows-x64'])
    parser.add_argument('--engine', required=True, type=Path)
    args = parser.parse_args()
    try:
        assemble(args.engine.resolve())
    except (ValueError, OSError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        parser.exit(1, f'Build failed: {exc}\n')


if __name__ == '__main__':
    main()
