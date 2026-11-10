# Same Python 3.12 base digest as tsb-core's worker image at the extraction cut.
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
RUN apt-get update \
    && apt-get install -y --no-install-recommends git make bash ca-certificates openssh-client zip unzip \
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
