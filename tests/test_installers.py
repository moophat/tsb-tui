"""Installer acceptance proofs use disposable homes, assets and loopback only."""
import fnmatch
import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release(tmp_path):
    site = tmp_path / 'site'
    tag = site / 'v1.2.3'
    tag.mkdir(parents=True)
    for name in ['tsb_tui-1.2.3-py3-none-any.whl', 'tsb_contract-1.0-py3-none-any.whl']:
        (tag / name).write_bytes(b'fixture wheel')
    engine = tmp_path / 'libghostty-vt.so'
    engine.write_bytes(b'fixture engine')
    with tarfile.open(tag / 'libghostty-vt-linux-x86_64.tar.gz', 'w:gz') as archive:
        archive.add(engine, arcname=engine.name)
    (tag / 'install.sh').write_bytes((ROOT / 'install.sh').read_bytes())
    assets = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': p.stat().st_size}
              for p in tag.iterdir()}
    (tag / 'SHA256SUMS').write_text(''.join(f'{v["sha256"]}  {k}\n' for k, v in assets.items()))
    (site / 'latest.json').write_text(json.dumps(
        {'schema': 1, 'version': '1.2.3', 'tag': 'v1.2.3', 'dir': 'v1.2.3/', 'assets': assets}))
    requests = []
    redirects = set()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path in redirects:
                self.send_response(302)
                self.send_header('Location', '/redirect-target')
                self.end_headers()
                return
            super().do_GET()

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(site)))
    thread = threading.Thread(target=server.serve_forever)
    home = tmp_path / 'home'
    home.mkdir(exist_ok=True)
    bins = tmp_path / 'bin'
    bins.mkdir()
    calls = tmp_path / 'pipx-calls'
    venv = tmp_path / 'venvs' / 'tsb-tui'
    (venv / 'bin').mkdir(parents=True)
    for path in [venv / 'bin' / 'python', bins / 'tsb-tui']:
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    (bins / 'python3.13').symlink_to(sys.executable)
    # Stub only the transport: real HTTP goes to the loopback server, never another host.
    (bins / 'curl').write_text(
        '#!' + sys.executable + '\n'
        'import os, sys, urllib.parse\n'
        'urls = [a for a in sys.argv[1:] if a.startswith(("http://", "https://"))]\n'
        'assert len(urls) == 1 and urllib.parse.urlsplit(urls[0]).hostname in ("127.0.0.1", "::1", "localhost")\n'
        f'os.execv({shutil.which("curl")!r}, ["curl", *sys.argv[1:]])\n')
    (bins / 'curl').chmod(0o755)
    (bins / 'pipx').write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
        'if [ "$1" = environment ]; then\n'
        'case "$3" in PIPX_LOCAL_VENVS) echo "$VENVS";; PIPX_BIN_DIR) echo "$BINS";; esac\nfi\n')
    (bins / 'pipx').chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith('TSB_TUI_')}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'), PATH=f'{bins}:{env["PATH"]}',
               CALLS=str(calls), VENVS=str(venv.parent), BINS=str(bins))
    work = tmp_path / 'work'
    work.mkdir()
    base = f'http://127.0.0.1:{server.server_port}'
    thread.start()
    try:
        yield base, tag, env, work, calls, requests, redirects
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def run(release, *args, pinned=False):
    _base, _tag, env, work, _calls, _requests, _redirects = release
    if pinned:
        env['TSB_TUI_VERSION'] = '1.2.3'
    return subprocess.run(['bash', str(ROOT / 'install.sh'), *args], cwd=work, env=env,
                          capture_output=True, text=True, timeout=30, check=False)


def test_manifest_install(release):
    base, _, env, _, calls, requests, _ = release
    result = run(release, '--base', base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '/latest.json' in requests
    assert 'install --force' in calls.read_text()
    assert (Path(env['XDG_CONFIG_HOME']) / 'tsb-tui/config.toml').read_text() == f'[broker]\nurl = "{base}"\n'


def test_pin_never_reads_latest(release):
    result = run(release, '--base', release[0], pinned=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '/latest.json' not in release[5]
    assert '/v1.2.3/SHA256SUMS' in release[5]


def test_corrupt_asset_stops_before_pipx(release):
    (release[1] / 'tsb_tui-1.2.3-py3-none-any.whl').write_bytes(b'corrupt')
    result = run(release, '--base', release[0])
    assert result.returncode != 0
    assert '/v1.2.3/tsb_tui-1.2.3-py3-none-any.whl' in release[5]
    assert not release[4].exists()


def test_existing_config_untouched(release):
    config = Path(release[2]['XDG_CONFIG_HOME']) / 'tsb-tui/config.toml'
    config.parent.mkdir(parents=True)
    config.write_bytes(b'# hand edited\n')
    result = run(release, '--base', release[0])
    assert result.returncode == 0, result.stdout + result.stderr
    assert config.read_bytes() == b'# hand edited\n'


def test_literal_token_requires_base(release):
    result = run(release)
    assert result.returncode != 0
    assert '--base' in result.stderr
    assert not release[4].exists()
    assert not release[5]


@pytest.fixture
def macos(release):
    """The release site plus both macOS zips and a faked Darwin: uname and xattr are stubs."""
    tag, env = release[1], release[2]
    site = tag.parent
    manifest = json.loads((site / 'latest.json').read_text())
    for target in ('macos-arm64', 'macos-x64'):
        folder = f'tsb-tui-1.2.3-{target}'
        name = f'{folder}.zip'
        with zipfile.ZipFile(tag / name, 'w') as archive:
            for member, data in {'tsb-tui': (ROOT / 'packaging/launchers/tsb-tui').read_bytes(),
                                 'tsb-tui-check': (ROOT / 'packaging/launchers/tsb-tui-check').read_bytes(),
                                 # The bundle's Python is the test interpreter: it writes the first-run config.
                                 'python/bin/python3': f'#!/bin/sh\nexec {sys.executable} "$@"\n'.encode()}.items():
                info = zipfile.ZipInfo(f'{folder}/{member}')
                info.external_attr = 0o755 << 16
                archive.writestr(info, data)
        manifest['assets'][name] = {'sha256': hashlib.sha256((tag / name).read_bytes()).hexdigest(),
                                    'size': (tag / name).stat().st_size}
    (site / 'latest.json').write_text(json.dumps(manifest))
    bins = Path(env['BINS'])
    (bins / 'uname').write_text('#!/bin/sh\ncase "$1" in -s) echo Darwin;; -m) echo "${FAKE_ARCH:-arm64}";; esac\n')
    xattr_log = bins.parent / 'xattr-calls'
    (bins / 'xattr').write_text(f'#!/bin/sh\necho "xattr $*" >> "{xattr_log}"\n')
    # plutil as macOS ships it, for the two forms the installer uses: `-type KEY FILE` prints the
    # type of a top-level value, `-extract KEY raw|json -o - FILE` prints the value itself.
    (bins / 'plutil').write_text(
        '#!' + sys.executable + '\n'
        'import json, sys\n'
        'args = sys.argv[1:]\n'
        'try:\n'
        '    data = json.load(open(args[-1]))\n'
        '    value = data[args[1]]\n'
        'except (OSError, ValueError, KeyError, IndexError):\n'
        '    sys.exit(1)\n'
        'names = {bool: "bool", int: "integer", float: "float", str: "string", dict: "dictionary", list: "array"}\n'
        'if args[0] == "-type":\n'
        '    print(names[type(value)])\n'
        'elif args[0] == "-extract" and args[2] == "raw":\n'
        '    print(f"{value:f}" if isinstance(value, float) else value)\n'
        'elif args[0] == "-extract" and args[2] == "json":\n'
        '    print(json.dumps(value, separators=(",", ":")))\n'
        'else:\n'
        '    sys.exit(1)\n')
    for stub in ('uname', 'xattr', 'plutil'):
        (bins / stub).chmod(0o755)
    env['FAKE_ARCH'] = 'arm64'
    home = Path(env['HOME'])
    return release, home / '.local/share/tsb-tui', home / '.local/bin', xattr_log


@pytest.mark.parametrize('chip,target', [('arm64', 'macos-arm64'), ('x86_64', 'macos-x64')])
def test_macos_installs_the_zip_for_the_chip(macos, chip, target):
    rel, install, bindir, xattr_log = macos
    rel[2]['FAKE_ARCH'] = chip
    result = run(rel, '--base', rel[0])
    assert result.returncode == 0, result.stdout + result.stderr
    other = 'macos-x64' if target == 'macos-arm64' else 'macos-arm64'
    assert f'/v1.2.3/tsb-tui-1.2.3-{target}.zip' in rel[5]
    assert f'/v1.2.3/tsb-tui-1.2.3-{other}.zip' not in rel[5]
    assert not rel[4].exists(), 'macOS install never runs pipx'
    assert (install / 'tsb-tui').is_file() and (install / 'python/bin/python3').is_file()
    for program in ('tsb-tui', 'tsb-tui-check'):
        assert (bindir / program).is_symlink()
        assert (bindir / program).resolve() == (install / program).resolve()
    assert not (install / 'config.toml').exists()
    assert not (install / 'config.toml').exists()
    config = Path(rel[2]['XDG_CONFIG_HOME']) / 'tsb-tui/config.toml'
    assert config.read_text() == f'[broker]\nurl = "{rel[0]}"\n'
    assert 'No config written' not in result.stdout
    # The installer clears the flag on the installed folder before the first launch.
    assert xattr_log.read_text().splitlines()[0] == f'xattr -dr com.apple.quarantine {install}'
    assert 'is not on PATH' in result.stdout


def test_macos_bad_checksum_installs_nothing(macos):
    rel, install, bindir, xattr_log = macos
    zip_path = rel[1] / 'tsb-tui-1.2.3-macos-arm64.zip'
    data = bytearray(zip_path.read_bytes())
    data[-1] ^= 0xFF
    zip_path.write_bytes(bytes(data))
    result = run(rel, '--base', rel[0])
    assert result.returncode != 0
    assert 'mismatch' in result.stderr
    assert not install.exists() and not bindir.exists() and not xattr_log.exists()


def test_macos_update_keeps_config_beside_the_program(macos):
    rel, install, _, _ = macos
    install.mkdir(parents=True)
    (install / 'config.toml').write_bytes(b'# hand edited\n')
    (install / 'stale-file').write_text('from the old install')
    result = run(rel, '--base', rel[0])
    assert result.returncode == 0, result.stdout + result.stderr
    assert (install / 'config.toml').read_bytes() == b'# hand edited\n'
    assert not (install / 'stale-file').exists()
    assert (install / 'tsb-tui').is_file()
    assert not list(install.parent.glob('tsb-tui.*')), 'no temporary sibling is left behind'


def test_macos_existing_user_config_is_left_byte_identical(macos):
    rel = macos[0]
    config = Path(rel[2]['XDG_CONFIG_HOME']) / 'tsb-tui/config.toml'
    config.parent.mkdir(parents=True)
    config.write_bytes(b'# hand edited\r\n[broker]\nurl = "https://elsewhere"\n')
    before = config.read_bytes()
    result = run(rel, '--base', rel[0], '--broker', 'https://other')
    assert result.returncode == 0, result.stdout + result.stderr
    assert config.read_bytes() == before
    assert f'Keeping existing config: {config}' in result.stdout


def test_macos_broker_flag_overrides_the_origin(macos):
    rel = macos[0]
    result = run(rel, '--base', rel[0], '--broker', 'https://broker.example')
    assert result.returncode == 0, result.stdout + result.stderr
    config = Path(rel[2]['XDG_CONFIG_HOME']) / 'tsb-tui/config.toml'
    assert config.read_text() == '[broker]\nurl = "https://broker.example"\n'


def test_macos_chip_without_an_asset_is_refused_by_name(macos):
    rel, install, _, _ = macos
    site = rel[1].parent
    manifest = json.loads((site / 'latest.json').read_text())
    del manifest['assets']['tsb-tui-1.2.3-macos-arm64.zip']
    (site / 'latest.json').write_text(json.dumps(manifest, indent=2))
    result = run(rel, '--base', rel[0])
    assert result.returncode != 0
    assert 'tsb-tui-1.2.3-macos-arm64.zip' in result.stderr
    assert not install.exists()


def test_macos_does_not_overwrite_a_foreign_file_on_the_path(macos):
    rel, install, bindir, _ = macos
    bindir.mkdir(parents=True)
    (bindir / 'tsb-tui').write_text('not ours')
    result = run(rel, '--base', rel[0])
    assert result.returncode != 0
    assert (bindir / 'tsb-tui').read_text() == 'not ours'
    assert not install.exists()


def rebuild_arm64_zip(rel, drop=(), links=None):
    """Replace the arm64 zip: leave out some members, add symlink members {member: target}."""
    site = rel[1].parent
    folder = 'tsb-tui-1.2.3-macos-arm64'
    name = f'{folder}.zip'
    members = {'tsb-tui': (ROOT / 'packaging/launchers/tsb-tui').read_bytes(),
               'tsb-tui-check': (ROOT / 'packaging/launchers/tsb-tui-check').read_bytes(),
               'python/bin/python3': f'#!/bin/sh\nexec {sys.executable} "$@"\n'.encode()}
    with zipfile.ZipFile(rel[1] / name, 'w') as archive:
        for member, data in members.items():
            if member not in drop:
                info = zipfile.ZipInfo(f'{folder}/{member}')
                info.external_attr = 0o755 << 16
                archive.writestr(info, data)
        for member, target in (links or {}).items():
            info = zipfile.ZipInfo(f'{folder}/{member}')
            info.external_attr = (0o120000 | 0o777) << 16
            archive.writestr(info, target)
    manifest = json.loads((site / 'latest.json').read_text())
    manifest['assets'][name] = {'sha256': hashlib.sha256((rel[1] / name).read_bytes()).hexdigest(),
                                'size': (rel[1] / name).stat().st_size}
    (site / 'latest.json').write_text(json.dumps(manifest))


def rewrite_manifest(rel, change):
    path = rel[1].parent / 'latest.json'
    manifest = json.loads(path.read_text())
    change(manifest)
    path.write_text(json.dumps(manifest))


def _schema_as_float(m):
    m['schema'] = 1.0


def _schema_as_string(m):
    m['schema'] = '1'


def _schema_only_in_an_unrelated_value(m):
    del m['schema']
    m['extra'] = {'schema': 1}


def _tag_only_in_an_unrelated_value(m):
    m['tag'] = 'v9.9.9'
    m['extra'] = {'tag': 'v1.2.3'}


def _version_as_number_with_a_match_elsewhere(m):
    m['version'] = 1.2
    m['extra'] = {'version': '1.2.3'}


@pytest.mark.parametrize('change', [_schema_as_float, _schema_as_string, _schema_only_in_an_unrelated_value,
                                    _tag_only_in_an_unrelated_value, _version_as_number_with_a_match_elsewhere])
def test_macos_manifest_fields_are_checked_by_type_and_value(macos, change):
    rel, install, bindir, _ = macos
    rewrite_manifest(rel, change)
    result = run(rel, '--base', rel[0])
    assert result.returncode != 0
    assert 'Invalid latest.json' in result.stderr
    assert not install.exists() and not bindir.exists()


def test_macos_interrupted_update_restores_the_backup_first(macos):
    rel, install, _, _ = macos
    backup = install.parent / 'tsb-tui.old'
    backup.mkdir(parents=True)
    (backup / 'marker').write_text('the only copy')
    rebuild_arm64_zip(rel, drop=('tsb-tui-check',))
    result = run(rel, '--base', rel[0])
    assert result.returncode != 0
    assert 'missing tsb-tui-check' in result.stderr
    assert (install / 'marker').read_text() == 'the only copy'
    assert not backup.exists()
    assert 'Restored the previous install' in result.stdout


def test_macos_config_that_cannot_be_written_keeps_the_previous_install(macos):
    rel, install, bindir, _ = macos
    install.mkdir(parents=True)
    (install / 'marker').write_text('previous')
    blocker = Path(rel[2]['HOME']) / 'blocker'
    blocker.write_text('a file where the config folder should be')
    rel[2]['XDG_CONFIG_HOME'] = str(blocker / 'config')
    result = run(rel, '--base', rel[0])
    assert result.returncode != 0
    assert (install / 'marker').read_text() == 'previous'
    assert 'Installed at' not in result.stdout
    assert not (install.parent / 'tsb-tui.old').exists()
    assert not (bindir / 'tsb-tui').exists()


def test_macos_does_not_replace_another_programs_link(macos):
    rel, install, bindir, _ = macos
    bindir.mkdir(parents=True)
    other = bindir / 'other-program'
    other.write_text('someone else')
    (bindir / 'tsb-tui-check').symlink_to(other)
    result = run(rel, '--base', rel[0])
    assert result.returncode != 0
    assert 'is not a link' in result.stderr
    assert (bindir / 'tsb-tui-check').resolve() == other.resolve()
    assert not install.exists()


def test_macos_update_keeps_its_own_links(macos):
    rel, install, bindir, _ = macos
    assert run(rel, '--base', rel[0]).returncode == 0
    result = run(rel, '--base', rel[0])
    assert result.returncode == 0, result.stdout + result.stderr
    for program in ('tsb-tui', 'tsb-tui-check'):
        assert (bindir / program).resolve() == (install / program).resolve()


def test_macos_archive_link_cannot_redirect_the_kept_config(macos, tmp_path):
    rel, install, _, _ = macos
    victim = tmp_path / 'victim'
    victim.write_text('victim')
    install.mkdir(parents=True)
    (install / 'config.toml').write_bytes(b'# hand edited\n')
    rebuild_arm64_zip(rel, links={'config.toml': str(victim)})
    result = run(rel, '--base', rel[0])
    assert result.returncode != 0
    assert 'link' in result.stderr
    assert victim.read_text() == 'victim'
    assert (install / 'config.toml').read_bytes() == b'# hand edited\n'


def test_release_checksums_cover_uploads(tmp_path):
    """Execute the workflow's checksum commands over a disposable release directory."""
    workflow = (ROOT / '.github/workflows/release.yml').read_text()
    publish = workflow.split('      - name: Publish the release assets and checksums', 1)[1]
    patterns = set(re.findall(r'dist/([\w.*-]+)', publish))
    names = ['tsb_tui-1-py3-none-any.whl', 'tsb_contract-1-py3-none-any.whl', 'tsb-1.tar.gz',
             'libghostty-vt-linux-x86_64.tar.gz', 'install.sh', 'install.ps1',
             'tsb-tui-1-windows-x64.zip', 'tsb-tui-1-windows-x64.zip.sha256',
             'tsb-tui-1-macos-arm64.zip', 'tsb-tui-1-macos-arm64.zip.sha256',
             'tsb-tui-1-macos-x64.zip', 'tsb-tui-1-macos-x64.zip.sha256']
    assert all(any(fnmatch.fnmatch(n, p) for p in patterns) for n in names)
    for name in names:
        (tmp_path / name).write_text(name)
    commands = [line.strip() for line in workflow.splitlines()
                if line.strip().startswith('sha256sum ') and '> SHA256SUMS' in line]
    assert commands
    for command in commands:
        subprocess.run(['bash', '-ec', command], cwd=tmp_path, check=True)
    entries = [line.split()[1] for line in (tmp_path / 'SHA256SUMS').read_text().splitlines()]
    assert sorted(entries) == sorted(names)


def test_local_assets_without_broker(release):

    for asset in release[1].iterdir():
        shutil.copyfile(asset, release[3] / asset.name)
    result = run(release)
    assert result.returncode == 0, result.stdout + result.stderr
    config = Path(release[2]['XDG_CONFIG_HOME']) / 'tsb-tui/config.toml'
    assert not config.exists()
    assert str(config) in result.stdout and '--broker' in result.stdout
    assert not release[5]


@pytest.mark.parametrize('via_env', [False, True])
def test_local_broker_config(release, via_env):

    for asset in release[1].iterdir():
        shutil.copyfile(asset, release[3] / asset.name)
    broker = 'https://broker.example/path'
    if via_env:
        release[2]['TSB_TUI_BROKER'] = broker
        args = ()
    else:
        args = ('--broker', broker)
    result = run(release, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    config = Path(release[2]['XDG_CONFIG_HOME']) / 'tsb-tui/config.toml'
    assert tomllib.loads(config.read_text())['broker']['url'] == broker
    assert not release[5]


def test_base_and_broker_precedence(release):

    release[2]['TSB_TUI_BASE'] = release[0] + '/wrong'
    release[2]['TSB_TUI_BROKER'] = 'https://old.example'
    result = run(release, '--base', release[0], '--broker', 'https://selected.example')
    assert result.returncode == 0, result.stdout + result.stderr
    config = Path(release[2]['XDG_CONFIG_HOME']) / 'tsb-tui/config.toml'
    assert tomllib.loads(config.read_text())['broker']['url'] == 'https://selected.example'


def test_rendered_script_uses_origin(release):

    downloads = release[1].parent / 'downloads' / 'tsb-tui'
    downloads.mkdir(parents=True)
    shutil.copyfile(release[1].parent / 'latest.json', downloads / 'latest.json')
    shutil.copytree(release[1], downloads / 'v1.2.3')
    source = (ROOT / 'install.sh').read_text()
    script = release[3] / 'rendered.sh'
    script.write_text(source.replace('@@TSB_ORIGIN@@', release[0]))
    result = subprocess.run(['bash', str(script)], cwd=release[3], env=release[2],
                            check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '/downloads/tsb-tui/latest.json' in release[5]
    assert 'No download base' not in result.stderr


def test_windows_pure_functions(tmp_path):

    pwsh = shutil.which('pwsh')
    if pwsh is None:
        if os.environ.get('CI'):
            pytest.fail('CI image must contain the hash-pinned pwsh and Pester inputs')
        pytest.skip('run in the CI image for the hash-pinned pwsh and Pester inputs')
    config = "$c = New-PesterConfiguration; $c.Run.Path = $args[0]; $c.Run.Exit = $true; " \
             "$c.Output.Verbosity = 'Detailed'; $c.Output.RenderMode = 'Plaintext'; Invoke-Pester -Configuration $c"
    runner = tmp_path / 'pester.ps1'
    runner.write_text(config)
    result = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-File', str(runner),
                             str(ROOT / 'packaging/tests/installers.Tests.ps1')],
                            cwd=tmp_path, check=False, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r'Tests Passed: [1-9][0-9]*, Failed: 0, Skipped: 0', result.stdout)


def test_environment_base_and_broker_are_installer_only(release):
    release[2]['TSB_TUI_BASE'] = release[0]
    release[2]['TSB_TUI_BROKER'] = 'https://broker.example'
    bins = Path(release[2]['BINS'])
    (bins / 'tsb-tui').write_text(
        '#!/bin/sh\n[ -z "${TSB_TUI_BASE+x}${TSB_TUI_VERSION+x}${TSB_TUI_BROKER+x}" ]\n')
    result = run(release, pinned=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '/latest.json' not in release[5]
    assert '/v1.2.3/SHA256SUMS' in release[5]


def test_empty_existing_config_is_not_replaced(release):
    config = Path(release[2]['XDG_CONFIG_HOME']) / 'tsb-tui/config.toml'
    config.parent.mkdir(parents=True)
    config.touch()
    result = run(release, '--base', release[0])
    assert result.returncode == 0, result.stdout + result.stderr
    assert config.read_bytes() == b''


@pytest.mark.parametrize('base', ['http://example.invalid', 'http://127.0.0.2', 'http://localhost.example'])
def test_remote_http_refused(release, base):
    result = run(release, '--base', base)
    assert result.returncode != 0
    assert 'HTTPS' in result.stderr
    assert not release[4].exists()
    assert not release[5]


@pytest.mark.parametrize('transport', ['curl', 'wget'])
@pytest.mark.parametrize('path', ['/latest.json', '/v1.2.3/SHA256SUMS',
                                  '/v1.2.3/libghostty-vt-linux-x86_64.tar.gz'])
def test_redirect_refused(release, transport, path):
    if transport == 'wget':
        bins = Path(release[2]['BINS'])
        # A closed tool directory forces the fallback without changing the host.
        (bins / 'curl').unlink()
        for name in ['bash', 'uname', 'getconf', 'wget', 'mktemp', 'rm']:
            (bins / name).symlink_to(shutil.which(name))
        release[2]['PATH'] = str(bins)
    release[6].add(path)
    result = run(release, '--base', release[0])
    assert result.returncode != 0
    assert 'redirect' in result.stderr.lower()
    assert path in result.stderr
    assert '/redirect-target' not in release[5]
    assert not release[4].exists()


@pytest.mark.parametrize('asset', [False, True])
def test_windows_loopback_redirect_refused(release, asset):
    pwsh = shutil.which('pwsh')
    if pwsh is None:
        if os.environ.get('CI'):
            pytest.fail('CI image must contain the hash-pinned pwsh and Pester inputs')
        pytest.skip('run in the CI image for the hash-pinned pwsh and Pester inputs')
    release[6].add('/latest.json')
    script = release[3] / 'request.ps1'
    script.write_text(
        "param($Installer, $Url, $OutputFile)\n"
        ". $Installer\n"
        "try { Get-ReleaseFile $Url $OutputFile; exit 2 }\n"
        "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }\n")
    result = subprocess.run(
        [pwsh, '-NoProfile', '-NonInteractive', '-File', str(script),
         str(ROOT / 'install.ps1'), release[0] + '/latest.json',
         str(release[3] / 'archive.zip') if asset else ''],
        capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 1
    assert 'redirect' in result.stderr.lower()
    assert '/latest.json' in result.stderr
    assert '/redirect-target' not in release[5]


def test_release_checks_macos_before_publish():
    workflow = (ROOT / '.github/workflows/release.yml').read_text()
    steps = workflow.split('      - name: ')
    check = next(s for s in steps if s.startswith('Test the macOS zips'))
    assert workflow.index('Test the macOS zips') < workflow.index('Publish the release assets and checksums')
    assert 'test_macos_build.py::test_staged_macos_zips' in check
    assert 'macos' in check and 'packaging/out' in check


def test_release_containers_have_home_override():
    workflow = (ROOT / '.github/workflows/release.yml').read_text()
    for step in workflow.split('      - name: '):
        if 'ci-tui-py' in step and 'docker run' in step:
            for container in step.split('docker run')[1:]:
                command = container.split('ci-tui-py', 1)[0]
                assert '-e HOME=/tmp/home' in command, step.splitlines()[0]


def test_pr_macos_build_deselection():
    workflow = (ROOT / '.github/workflows/test.yml').read_text()
    section = workflow.split('      - name: Packaging tests', 1)[1].split('      - name: No test skips', 1)[0]
    assert '--deselect packaging/tests/test_packaging.py::test_built_zip' in section
    for case in (
        'packaging/tests/test_macos_build.py::test_macos_zip_end_to_end[macos-arm64]',
        'packaging/tests/test_macos_build.py::test_macos_zip_end_to_end[macos-x64]',
        'packaging/tests/test_macos_build.py::test_macos_readme_commands[macos-arm64]',
        'packaging/tests/test_macos_build.py::test_macos_readme_commands[macos-x64]',
        'packaging/tests/test_macos_build.py::test_staged_macos_zips[macos-arm64]',
        'packaging/tests/test_macos_build.py::test_staged_macos_zips[macos-x64]',
    ):
        assert f'--deselect "{case}"' in section, case
    for not_deselected in ('test_posix_launcher', 'test_macho_interpreter_architecture'):
        assert not any(not_deselected in line for line in section.splitlines() if '--deselect' in line), not_deselected
    assert "grep -E '^SKIPPED" in section
    assert 'release job builds the zips from the pinned inputs' in section
    assert 'checks them before publishing' in section
