"""A best-effort release notice; never a broker API call or a self-updater."""

from __future__ import annotations

import importlib.metadata
import ipaddress
import json
import math
import os
import queue
import re
import ssl
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from .saved_theme import state_path

TIMEOUT_S = 1.5
MAX_BYTES = 64 * 1024
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def parse_version(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        return None
    try:
        major, minor, patch = map(int, value.split("."))
        return major, minor, patch
    except ValueError:
        return None


def manifest_url(broker: str, cfg) -> str:
    if cfg["update.url"]:
        return cfg["update.url"]
    scheme, separator, rest = broker.partition("://")
    authority = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return f"{scheme}{separator}{authority}/downloads/tsb-tui/latest.json"


def _safe_url(url: str) -> bool:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~:/%"
    if not url or any(char not in allowed for char in url):
        return False
    scheme, separator, rest = url.partition("://")
    authority, slash, path = rest.partition("/")
    if not separator or not authority or not slash or not ("/" + path).endswith("/latest.json"):
        return False
    if scheme == "https":
        return True
    if scheme != "http":
        return False
    host = authority.split(":", 1)[0]
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_latest(url: str, ca_file: str | None = None, *, version: str | None = None) -> str | None:
    """One unauthenticated GET, without redirects; bound the caller's entire wait, including DNS.

    urllib's timeout bounds individual socket operations rather than the whole request. A daemon
    performs the I/O and sends only a result back; the caller stops waiting at the total deadline.
    The I/O thread never touches UI or cache, and checks the deadline between bounded reads.
    """
    if not _safe_url(url):
        return None
    if version is None:
        try:
            version = importlib.metadata.version("tsb-tui")
        except importlib.metadata.PackageNotFoundError:
            return None
    deadline = time.monotonic() + TIMEOUT_S
    result: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def request() -> None:
        latest = None
        try:
            context = ssl.create_default_context(cafile=ca_file)
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirect(),
                urllib.request.HTTPSHandler(context=context))
            req = urllib.request.Request(url, headers={"User-Agent": f"tsb-tui/{version}"})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            with opener.open(req, timeout=remaining) as response:
                data = bytearray()
                while len(data) < MAX_BYTES and time.monotonic() < deadline:
                    chunk = response.read1(MAX_BYTES - len(data))
                    if not chunk:
                        doc = json.loads(data)
                        if (isinstance(doc, dict) and type(doc.get("schema")) is int
                                and doc["schema"] == 1 and parse_version(doc.get("version")) is not None):
                            latest = doc["version"]
                        break
                    data.extend(chunk)
        except Exception:  # noqa: BLE001, S110 — every failed optional check must stay silent
            # This optional static-file check is silent for every transport/document failure.
            # Keep the boundary here: failures cannot escape through a background thread.
            pass
        finally:
            result.put(latest)

    threading.Thread(target=request, name="tsb-update-fetch", daemon=True).start()
    try:
        return result.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        return None


def load_cache(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        checked = doc.get("checked_at") if isinstance(doc, dict) else None
        if type(checked) not in (int, float) or not math.isfinite(checked):
            return {}
        return doc
    except (OSError, ValueError, RecursionError):
        return {}


def store_cache(path: Path, checked_at: float, latest: str | None, url: str) -> None:
    """Atomic per-user cache, following saved_theme's permission and replace discipline."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump({"checked_at": checked_at, "latest": latest, "url": url}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def decide(broker: str, cfg, version: str, ca_file: str | None = None, *,
           cache_path: Path | None = None, now: float | None = None) -> str | None:
    running = parse_version(version)
    if not cfg["update.check"] or version == "0.0.0" or running is None:
        return None
    url = manifest_url(broker, cfg)
    if not _safe_url(url):
        return None
    path = cache_path if cache_path is not None else state_path().with_name("update-check.json")
    timestamp = time.time() if now is None else now
    cached = load_cache(path)
    if cached.get("url") != url:
        cached = {}
    latest = cached.get("latest")
    elapsed = timestamp - cached.get("checked_at", -math.inf)
    if elapsed < 0 or elapsed >= cfg["update.interval_h"] * 3600:
        fetched = fetch_latest(url, ca_file, version=version)
        if fetched is not None:
            latest = fetched
        try:
            store_cache(path, timestamp, latest, url)
        except OSError:
            pass  # A read-only state home must not disrupt startup or hide a known update.
    parsed = parse_version(latest)
    return latest if parsed is not None and parsed > running else None


def notice(latest: str, running: str, url: str) -> str:
    base = url.rsplit("/", 1)[0] + "/"
    command = (f"irm {base}install.ps1 | iex" if sys.platform == "win32"
               else f"curl -fsSL {base}install.sh | bash")
    return f"tsb-tui {latest} is available (you have {running}). Update: {command}"
