"""Broker selection at the three entry points; no UI or broker is started."""
from types import SimpleNamespace

import pytest

from tsb_tui import __main__ as entry
from tsb_tui import app, smoke
from tsb_tui.drive import runner


@pytest.fixture
def broker_file(tmp_path, monkeypatch):
    directory = tmp_path / "config" / "tsb-tui"
    directory.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(directory.parent))
    path = directory / "config.toml"
    path.write_text('[broker]\nurl = "https://configured"\n')
    return path


@pytest.mark.parametrize("flags,expected", [([], "https://configured"),
                                          (["--broker", "https://flag"], "https://flag"),
                                          (["--set", "broker.url=https://set"], "https://set")])
def test_main_broker(broker_file, monkeypatch, flags, expected):
    received = []
    monkeypatch.setattr(app, "TsbTuiApp", lambda broker, **kw: (
        received.append(broker) or SimpleNamespace(run=lambda: None, return_code=0)))
    assert entry.main(flags) == 0
    assert received == [expected]


def test_smoke_broker(broker_file, monkeypatch):
    received = []
    monkeypatch.setattr(smoke, "run", lambda broker, target, ca, **kw: (
        received.append((broker, target, kw)) or 0))
    assert entry.main(["--smoke", "--target", "target"]) == 0
    assert received[0][:2] == ("https://configured", "target")
    assert received[0][2]["broker_source"] == f"config file {broker_file}"


def test_drive_broker(broker_file, monkeypatch):
    received = []

    async def run(args, *unused):
        received.append(args.broker)
        return 0

    monkeypatch.setattr(runner, "_run", run)
    assert runner.main([]) == 0
    assert received == ["https://configured"]


def test_drive_guide_without_broker(capsys):
    assert runner.main(["--guide"]) == 0
    assert capsys.readouterr().out


@pytest.mark.parametrize("flags", [[], ["--smoke", "--target", "target"], ["drive"]])
def test_missing_broker(flags, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert entry.main(flags) == 2
    message = capsys.readouterr().err
    assert "no broker address" in message
    assert str(tmp_path / "tsb-tui" / "config.toml") in message
    assert "TSB_TUI_BROKER_URL" in message


@pytest.mark.parametrize("drive", [False, True])
def test_invalid_knob_broker_names_source(broker_file, drive, capsys):
    broker_file.write_text('[broker]\nurl = "http://example.org"\n')
    assert entry.main(["drive"] if drive else []) == 1
    assert f"broker.url (config file {broker_file}):" in capsys.readouterr().err
