"""Phase 9: update notices use only a bounded, unauthenticated manifest request."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tsb_tui import config
from tsb_tui import update_check as update
from tsb_tui.app import TsbTuiApp


@contextmanager
def manifest_server(body=b'{"schema":1,"version":"0.10.0"}', *, status=200, delay=0, location=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            time.sleep(delay)
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def cfg(tmp_path, *, profile=None, overrides=(), environ=None):
    return config.load(config_file=tmp_path / "absent.toml", profile=profile,
                       overrides=overrides, environ=environ or {})


@pytest.mark.parametrize("value", ["v1.2.3", "1.2.3rc1", "a.2.3", "1.2.3.4", "1.2", "١.2.3", None, 123])
def test_invalid_versions(value):
    assert update.parse_version(value) is None


def test_version_order():
    assert update.parse_version("0.4.3") < update.parse_version("0.10.0")


@pytest.mark.parametrize("body,expected", [
    (b'{"schema":1,"version":"0.10.0"}', "0.10.0"),
    (b'{"schema":1,"version":"0.4.3"}', None),
    (b'{"schema":1,"version":"0.1.0"}', None),
    (b'not json', None), (b'{"schema":2,"version":"2.0.0"}', None),
    (b'{"schema":1}', None), (b' ' * 65536 + b'{}', None),
])
def test_answers(tmp_path, body, expected):
    with manifest_server(body) as (broker, requests):
        path = tmp_path / "cache.json"
        assert update.decide(broker, cfg(tmp_path), "0.4.3", cache_path=path) == expected
        assert len(requests) == 1
        assert json.loads(path.read_text())["checked_at"] > 0


def test_headers_and_redirect(tmp_path):
    with manifest_server() as (other, redirected):
        with manifest_server(status=302, location=other + "/elsewhere") as (broker, requests):
            assert update.decide(broker, cfg(tmp_path), "0.4.3", cache_path=tmp_path / "cache") is None
            headers = {key.lower(): value for key, value in requests[0][1].items()}
            assert headers["user-agent"] == "tsb-tui/0.4.3"
            assert "authorization" not in headers and "cookie" not in headers
        assert redirected == []


def test_cache_reuse_expiry_and_corruption(tmp_path):
    path = tmp_path / "cache"
    with manifest_server() as (broker, requests):
        resolved = cfg(tmp_path)
        assert update.decide(broker, resolved, "0.4.3", cache_path=path, now=100000) == "0.10.0"
        assert update.decide(broker, resolved, "0.4.3", cache_path=path, now=100001) == "0.10.0"
        assert len(requests) == 1
        assert update.decide(broker, resolved, "0.4.3", cache_path=path, now=200000) == "0.10.0"
        assert len(requests) == 2
        path.write_text("broken")
        assert update.decide(broker, resolved, "0.4.3", cache_path=path, now=200001) == "0.10.0"
        assert len(requests) == 3


@pytest.mark.parametrize("previous", [None, "0.10.0"])
@pytest.mark.parametrize("status,body", [(404, b"missing"), (200, b"bad json")])
def test_failed_request_keeps_known_latest_or_writes_null(tmp_path, previous, status, body):
    path = tmp_path / "cache"
    with manifest_server(body, status=status) as (broker, requests):
        url = update.manifest_url(broker, cfg(tmp_path))
        if previous:
            path.write_text(json.dumps({"checked_at": 1, "latest": previous, "url": url}))
        assert update.decide(broker, cfg(tmp_path), "0.4.3", cache_path=path, now=100000) == previous
        assert json.loads(path.read_text()) == {"checked_at": 100000, "latest": previous, "url": url}
        assert update.decide(broker, cfg(tmp_path), "0.4.3", cache_path=path, now=100001) == previous
        assert len(requests) == 1


@pytest.mark.parametrize("latest", [None, "invalid", "0.1.0", "0.4.3"])
def test_cached_non_newer_is_silent(tmp_path, latest):
    path = tmp_path / "cache"
    with manifest_server() as (broker, requests):
        url = update.manifest_url(broker, cfg(tmp_path))
        path.write_text(json.dumps({"checked_at": 100000, "latest": latest, "url": url}))
        assert update.decide(broker, cfg(tmp_path), "0.4.3", cache_path=path, now=100001) is None
        assert not requests


def test_success_replaces_previous_even_when_older(tmp_path):
    path = tmp_path / "cache"
    with manifest_server(b'{"schema":1,"version":"0.1.0"}') as (broker, _requests):
        path.write_text(json.dumps({"checked_at": 1, "latest": "9.0.0",
                                    "url": update.manifest_url(broker, cfg(tmp_path))}))
        assert update.decide(broker, cfg(tmp_path), "0.4.3", cache_path=path, now=100000) is None
        assert json.loads(path.read_text())["latest"] == "0.1.0"


@pytest.mark.parametrize("profile,overrides,environ,version", [
    (None, ["update.check=false"], {}, "0.4.3"),
    (None, [], {"TSB_TUI_UPDATE_CHECK": "0"}, "0.4.3"),
    ("drive", [], {}, "0.4.3"), ("harness", [], {}, "0.4.3"),
    (None, [], {}, "0.0.0"),
])
def test_disabled_and_development_make_no_request_or_write(tmp_path, profile, overrides, environ, version):
    with manifest_server() as (broker, requests):
        path = tmp_path / "cache"
        resolved = cfg(tmp_path, profile=profile, overrides=overrides, environ=environ)
        assert update.decide(broker, resolved, version, cache_path=path) is None
        assert not requests and not path.exists()


def test_urls_and_no_cleartext_non_loopback(tmp_path, monkeypatch):
    resolved = cfg(tmp_path)
    assert update.manifest_url("https://broker.example/base?q=x", resolved) == (
        "https://broker.example/downloads/tsb-tui/latest.json")
    custom = cfg(tmp_path, overrides=["update.url=https://updates.example/channel/latest.json"])
    assert update.manifest_url("https://broker.example", custom) == "https://updates.example/channel/latest.json"
    monkeypatch.setattr(update, "fetch_latest", lambda *_args, **_kwargs: pytest.fail("unsafe request"))
    assert update.decide("http://broker.example", resolved, "0.4.3", cache_path=tmp_path / "cache") is None


@pytest.mark.parametrize("platform,command", [
    ("win32", "irm https://broker.example/downloads/tsb-tui/install.ps1 | iex"),
    ("linux", "curl -fsSL https://broker.example/downloads/tsb-tui/install.sh | bash"),
])
def test_notice_command(platform, command, monkeypatch):
    monkeypatch.setattr(update.sys, "platform", platform)
    assert update.notice("0.10.0", "0.4.3", "https://broker.example/downloads/tsb-tui/latest.json") == (
        f"tsb-tui 0.10.0 is available (you have 0.4.3). Update: {command}")


def test_slow_request_does_not_block_login_and_worker_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.4.3")

    async def check(broker, requests):
        app = TsbTuiApp(broker, cfg=cfg(tmp_path, environ=dict(os.environ)))
        started = time.monotonic()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.screen.query("#login-panel")
            await pilot.click("#username")
            await pilot.press("a")
            assert app.screen.query_one("#username").value == "a"
            assert app._update_worker.is_running
            while app._update_worker.is_running and time.monotonic() - started < 2.5:
                await asyncio.sleep(0.02)
            assert not app._update_worker.is_running
            assert time.monotonic() - started < 2.5
            assert app.update_notice is None
            assert len(requests) == 1

    with manifest_server(delay=5) as (broker, requests):
        asyncio.run(check(broker, requests))


def test_fresh_and_cached_notice_toast_and_copyable_settings(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from textual.selection import Selection
    from textual.widgets import Static

    from tsb_tui.screens.chrome_dialogs import TextScreen, settings_lines

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.4.3")

    async def check(broker):
        resolved = cfg(tmp_path, environ=dict(os.environ))
        app = TsbTuiApp(broker, cfg=resolved)
        toasts = []
        monkeypatch.setattr(app, "toast", lambda text, **kwargs: toasts.append((text, kwargs)))
        async with app.run_test() as pilot:
            deadline = time.monotonic() + 2.5
            while app.update_notice is None and time.monotonic() < deadline:
                await pilot.pause(0.02)
            assert app.update_notice == update.notice("0.10.0", "0.4.3", update.manifest_url(broker, resolved))
            assert len(toasts) == 1
            assert toasts[0][1]["severity"] == "information"
            assert "Settings" in toasts[0][0]
            terminal = SimpleNamespace(dialogs=[], refresh_status=lambda: None)
            dialog = TextScreen(terminal, "Settings · the knobs in force, and where each came from",
                                lambda: settings_lines(resolved))
            app.push_screen(dialog)
            await pilot.pause()
            body = dialog.query_one("#text-body", Static)
            assert body.allow_select
            selected = body.get_selection(Selection(None, None))
            assert selected is not None and app.update_notice in selected[0]
            assert "update.check" in str(body.content)
            await pilot.pause()
            assert len(toasts) == 1

    with manifest_server() as (broker, requests):
        asyncio.run(check(broker))
        asyncio.run(check(broker))
        assert len(requests) == 1


def test_development_metadata_makes_no_request(tmp_path, monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.0.0")

    async def check(broker):
        app = TsbTuiApp(broker, cfg=cfg(tmp_path, environ=dict(os.environ)))
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.update_notice is None
            assert not app._update_worker.is_running

    with manifest_server() as (broker, requests):
        asyncio.run(check(broker))
        assert requests == []


def test_cache_without_latest_announces_nothing(tmp_path):
    path = tmp_path / "cache"
    with manifest_server() as (broker, requests):
        path.write_text(json.dumps({"checked_at": 100000, "url": update.manifest_url(broker, cfg(tmp_path))}))
        assert update.decide(broker, cfg(tmp_path), "0.4.3", cache_path=path, now=100001) is None
        assert not requests


def test_cache_permissions_and_failed_replace_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "state" / "update-check.json"
    url = "https://broker.example/latest.json"
    update.store_cache(path, 100000, "0.10.0", url)
    assert update.load_cache(path) == {"checked_at": 100000, "latest": "0.10.0", "url": url}
    if hasattr(os, "fchmod"):
        assert path.stat().st_mode & 0o777 == 0o600

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(update.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        update.store_cache(path, 200000, None, url)
    assert update.load_cache(path)["latest"] == "0.10.0"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("previous", [None, "0.10.0"])
def test_timeout_retains_latest_and_caches_attempt(tmp_path, previous):
    path = tmp_path / "cache"
    with manifest_server(delay=5) as (broker, requests):
        url = update.manifest_url(broker, cfg(tmp_path))
        if previous:
            path.write_text(json.dumps({"checked_at": 1, "latest": previous, "url": url}))
        started = time.monotonic()
        assert update.decide(broker, cfg(tmp_path), "0.4.3", cache_path=path, now=100000) == previous
        assert time.monotonic() - started < 2
        assert json.loads(path.read_text()) == {"checked_at": 100000, "latest": previous, "url": url}
        assert len(requests) == 1


def test_override_manifest_is_requested(tmp_path):
    with manifest_server() as (broker, broker_requests), manifest_server() as (other, other_requests):
        resolved = cfg(tmp_path, overrides=[f"update.url={other}/channel/latest.json"])
        assert update.decide(broker, resolved, "0.4.3", cache_path=tmp_path / "cache") == "0.10.0"
        assert not broker_requests
        assert other_requests[0][0] == "/channel/latest.json"


@pytest.mark.parametrize("override", [False, True])
def test_qodo_cache_is_scoped_to_manifest_url(tmp_path, override):
    path = tmp_path / "cache"
    with manifest_server() as (first, first_requests), manifest_server(
            b'{"schema":1,"version":"0.4.3"}') as (second, second_requests):
        initial = cfg(tmp_path)
        assert update.decide(first, initial, "0.4.3", cache_path=path, now=100000) == "0.10.0"
        resolved = cfg(tmp_path, overrides=[f"update.url={second}/channel/latest.json"]) if override else initial
        broker = first if override else second
        assert update.decide(broker, resolved, "0.4.3", cache_path=path, now=100001) is None
        assert len(first_requests) == len(second_requests) == 1
        assert json.loads(path.read_text())["url"] == update.manifest_url(broker, resolved)


@pytest.mark.parametrize("suffix", ["?token=x", "#section", "/other.json", "/latest.json/", ";echo", "'", '"',
                                     " ", "\t", "\n", "$(echo)", "&", "|", "`", "\\", "é"])
def test_qodo_invalid_manifest_disables_fresh_and_cached_check(tmp_path, monkeypatch, suffix):
    path = tmp_path / "cache"
    url = "https://updates.example/channel/latest.json" + suffix
    resolved = cfg(tmp_path, overrides=[f"update.url={url}"])
    monkeypatch.setattr(update, "fetch_latest", lambda *_args, **_kwargs: pytest.fail("invalid URL fetched"))
    for cached in (False, True):
        if cached:
            path.write_text(json.dumps({"checked_at": 100000, "latest": "9.0.0", "url": url}))
        before = path.read_bytes() if path.exists() else None
        assert update.decide("https://broker.example", resolved, "0.4.3", cache_path=path, now=100001) is None
        assert (path.read_bytes() if path.exists() else None) == before


def test_qodo_non_loopback_http_disables_cached_notice(tmp_path, monkeypatch):
    path = tmp_path / "cache"
    url = "http://updates.example/latest.json"
    path.write_text(json.dumps({"checked_at": 100000, "latest": "9.0.0", "url": url}))
    monkeypatch.setattr(update, "fetch_latest", lambda *_args, **_kwargs: pytest.fail("cleartext request"))
    assert update.decide("https://broker.example", cfg(tmp_path, overrides=[f"update.url={url}"]),
                         "0.4.3", cache_path=path, now=100001) is None


@pytest.mark.parametrize("url", [
    "http://[::1]:8080/downloads/tsb-tui/latest.json",
    "https://[2001:db8::1]/downloads/tsb-tui/latest.json",
])
def test_qodo_ipv6_manifest_url_is_fetched(tmp_path, monkeypatch, url):
    path = tmp_path / "cache"
    requested = []
    monkeypatch.setattr(update, "fetch_latest", lambda *args, **_kwargs: requested.append(args[0]) or None)
    resolved = cfg(tmp_path, overrides=[f"update.url={url}"])
    assert update._safe_url(url) is True
    assert update.decide("https://broker.example", resolved, "0.4.3", cache_path=path, now=100000) is None
    assert requested == [url]
    assert json.loads(path.read_text())["url"] == url


@pytest.mark.parametrize("url", [
    "http://[2001:db8::1]/downloads/tsb-tui/latest.json",
    "https://updates.example/channel[1]/latest.json",
    "https://updates.example/channel]/latest.json",
    "https://[::1/downloads/tsb-tui/latest.json",
    "https://::1]/downloads/tsb-tui/latest.json",
    "https://[::1]]/downloads/tsb-tui/latest.json",
    "https://[[::1]/downloads/tsb-tui/latest.json",
    "https://[]/downloads/tsb-tui/latest.json",
])
def test_qodo_misplaced_bracket_or_non_loopback_ipv6_is_refused(tmp_path, monkeypatch, url):
    path = tmp_path / "cache"
    monkeypatch.setattr(update, "fetch_latest", lambda *_args, **_kwargs: pytest.fail("invalid URL fetched"))
    resolved = cfg(tmp_path, overrides=[f"update.url={url}"])
    assert update._safe_url(url) is False
    assert update.decide("https://broker.example", resolved, "0.4.3", cache_path=path, now=100001) is None
    assert not path.exists()


def test_qodo_future_cache_is_due(tmp_path):
    path = tmp_path / "cache"
    with manifest_server() as (broker, requests):
        resolved = cfg(tmp_path)
        url = update.manifest_url(broker, resolved)
        path.write_text(json.dumps({"checked_at": 200000, "latest": "0.4.3", "url": url}))
        assert update.decide(broker, resolved, "0.4.3", cache_path=path, now=100000) == "0.10.0"
        assert len(requests) == 1
