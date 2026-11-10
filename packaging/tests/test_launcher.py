"""The Windows launcher: its command-line split on the host, its build and PE shape with the pinned cross-compiler."""
import ctypes
import hashlib
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
LAUNCHERS = HERE / 'launchers'
BUILD = HERE / 'build-launcher.sh'
EXE = HERE / 'cache' / 'tsb-tui.exe'

needs_cross = pytest.mark.skipif(
    shutil.which('x86_64-w64-mingw32-gcc') is None,
    reason='run inside the CI image, which pins the mingw-w64 cross-compiler')


@pytest.fixture(scope='module')
def split(tmp_path_factory):
    compiler = shutil.which('gcc') or shutil.which('cc')
    if compiler is None:
        pytest.skip('no host C compiler for the command-line split')
    library = tmp_path_factory.mktemp('cmdline') / 'cmdline.so'
    subprocess.run([compiler, '-shared', '-fPIC', '-Wall', '-Werror', str(LAUNCHERS / 'cmdline.c'),
                    '-o', str(library)], check=True)
    function = ctypes.CDLL(str(library)).tsb_cmdline_rest
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_void_p

    def rest(command):
        buffer = ctypes.create_unicode_buffer(command)
        pointer = function(buffer)
        offset = (pointer - ctypes.addressof(buffer)) // ctypes.sizeof(ctypes.c_wchar)
        assert 0 <= offset <= len(command)
        return command[offset:]

    return rest


@pytest.mark.parametrize('command,expected', [
    ('tsb-tui', ''),
    ('tsb-tui ', ''),
    ('"C:\\Program Files\\x\\tsb-tui.exe"', ''),
    ('"C:\\Program Files\\x\\tsb-tui.exe" --broker https://h', '--broker https://h'),
    ('"C:\\Program Files\\x\\tsb-tui.exe"   --broker https://h', '--broker https://h'),
    ('tsb-tui\t--broker\thttps://h', '--broker\thttps://h'),
    ('tsb-tui \t a&b', 'a&b'),
    ('tsb-tui ^', '^'),
    ('tsb-tui %PATH%', '%PATH%'),
    ('tsb-tui "a b" c  d ', '"a b" c  d '),
    ('tsb-tui a "', 'a "'),
    ('tsb-tui "', '"'),
    ('C:\\Program Files\\x\\tsb-tui.exe --broker h', 'Files\\x\\tsb-tui.exe --broker h'),
    ('"C:\\unterminated', ''),
    ('"C:\\a\\tsb-tui.exe"--broker h', '--broker h'),
    ('', ''),
])
def test_command_line_remainder_is_verbatim(split, command, expected):
    assert split(command) == expected


def build():
    EXE.unlink(missing_ok=True)
    subprocess.run(['bash', str(BUILD)], check=True)
    return hashlib.sha256(EXE.read_bytes()).hexdigest()


@needs_cross
def test_build_is_deterministic():
    first = build()
    second = build()
    assert first == second


def pe_headers(data):
    assert data[:2] == b'MZ'
    offset = struct.unpack_from('<I', data, 0x3C)[0]
    assert data[offset:offset + 4] == b'PE\0\0'
    machine, _, _, _, _, optional_size, characteristics = struct.unpack_from('<HHIIIHH', data, offset + 4)
    magic = struct.unpack_from('<H', data, offset + 24)[0]
    subsystem = struct.unpack_from('<H', data, offset + 24 + 68)[0]
    return machine, characteristics, magic, subsystem


@needs_cross
def test_executable_is_a_static_x64_console_program():
    build()
    machine, characteristics, magic, subsystem = pe_headers(EXE.read_bytes())
    assert machine == 0x8664
    assert magic == 0x20B
    assert not characteristics & 0x2000, 'must not be a DLL'
    assert characteristics & 0x0002, 'must be executable'
    assert subsystem == 3, 'must be a console program'
    dump = subprocess.run(['x86_64-w64-mingw32-objdump', '-p', str(EXE)],
                          check=True, capture_output=True, text=True).stdout
    imports = {line.split(':', 1)[1].strip().lower() for line in dump.splitlines() if 'DLL Name:' in line}
    assert 'kernel32.dll' in imports
    assert all(name in ('kernel32.dll', 'msvcrt.dll') or name.startswith('api-ms-win-')
               for name in imports), imports
    assert not [name for name in imports if name.startswith(('libgcc', 'libwinpthread', 'libstdc'))]
