"""Verify locked downloads before any consumer opens them."""
import argparse
import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def entries(lock, target=None):
    for number, line in enumerate(lock.read_text().splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError(f'{lock.name}:{number}: expected kind target URL sha256 size')
        kind, target_name, url, digest, size = fields
        if kind not in {'python', 'wheel', 'build'} or target_name not in {'windows-x64', 'macos-arm64', 'macos-x64'}:
            raise ValueError(f'{lock.name}:{number}: unsupported kind or target')
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError(f'{lock.name}:{number}: invalid sha256')
        if target is None or target_name == target:
            yield number, kind, url, digest, int(size)


def verify(lock, cache, fetch=False, target=None):
    rows = list(entries(lock, target))
    cache.mkdir(parents=True, exist_ok=True)
    for number, kind, url, digest, size in rows:
        path = cache / digest
        label = f'{lock.name}:{number}: {urllib.parse.unquote(url.rsplit("/", 1)[-1])}'
        if not path.exists() and fetch:
            temporary = path.with_suffix('.part')
            try:
                urllib.request.urlretrieve(url, temporary)
                with temporary.open('rb') as stream:
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                if temporary.stat().st_size != size or actual != digest:
                    raise ValueError(f'{label}: downloaded size or sha256 mismatch')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        if not path.is_file():
            raise ValueError(f'{label}: cached input missing')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != size or actual != digest:
            raise ValueError(f'{label}: cached input size or sha256 mismatch')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=HERE / 'inputs.lock')
    parser.add_argument('--cache', type=Path, default=HERE / 'cache')
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    try:
        rows = verify(args.lock, args.cache, args.fetch)
    except (ValueError, OSError) as exc:
        parser.exit(1, f'{exc}\n')
    print(f'Inputs verified: {len(rows)}')


if __name__ == '__main__':
    main()
