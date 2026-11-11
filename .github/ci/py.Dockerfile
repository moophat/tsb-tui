# Same Python 3.12 base digest as tsb-core's worker image at the extraction cut.
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
# curl and wget are test transports, not build inputs; use bookworm's current versions.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git make bash ca-certificates openssh-client zip unzip \
        curl wget \
        gcc=4:12.2.0-3 gcc-12=12.2.0-14+deb12u1 libc6-dev=2.36-9+deb12u14 \
        gcc-mingw-w64-x86-64-win32=12.2.0-14+deb12u1+25.2+b1 \
        binutils-mingw-w64-x86-64=2.40-2+10.4 \
        mingw-w64-x86-64-dev=10.0.0-3 \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
WORKDIR /src
COPY pyproject.toml README.md /src/
COPY tsb_tui /src/tsb_tui
# Installs the exact runtime and pytest pins from this repo's manifest.
RUN pip install --no-cache-dir --no-compile -e '.[test]' ruff==0.16.9 setuptools==84.2.1

# No Microsoft package repository: both test inputs are immutable, SHA-256-checked archives.
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
RUN python - <<'PYTHON'
import hashlib
import io
import os
import tarfile
import urllib.request
import zipfile
from pathlib import Path

inputs = [
    ('https://github.com/PowerShell/PowerShell/releases/download/v7.4.13/powershell-7.4.13-linux-x64.tar.gz',
     '59e5df675dacbfe45374c32c1bf2480168a33243423c9b19252d0476fd1b748c', 'pwsh'),
    ('https://www.powershellgallery.com/api/v2/package/Pester/5.7.1',
     '4a27904c6814a5fbe4758f8e49861f6a1994aee77b71165a5c43c0371ba6c580', 'pester'),
]
for url, digest, kind in inputs:
    with urllib.request.urlopen(url) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit(f'Input SHA-256 mismatch: {kind}')
    if kind == 'pwsh':
        destination = Path('/usr/local/lib/powershell')
        destination.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            archive.extractall(destination, filter='data')
        (destination / 'pwsh').chmod(0o755)
        os.symlink(destination / 'pwsh', '/usr/local/bin/pwsh')
    else:
        destination = Path('/usr/local/share/powershell/Modules/Pester/5.7.1')
        destination.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(destination)
PYTHON
RUN pwsh -NoProfile -NonInteractive -Command "Import-Module Pester -RequiredVersion 5.7.1 -ErrorAction Stop"
