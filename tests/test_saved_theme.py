"""The saved choice per TSB login, and Save (phase 10, ruling 1, TT11, TT16-TT19).

Why these matter (Rule 9): the picker's Save is the only way to keep a look for this login; if the state
file did not round-trip, Save would corrupt the next start; if a higher layer did not win, a config file's
choice would be silently overwritten; if Save wrote every login, one person's pick would change another's
start; and if drive read the file, an agent's `--set` look would be replaced by a human's saved default.

The fixture server (DT7) allows USER, PEER and VIEWER with one password, so two logins in one run go
through `_to_login`; no test touches a real home - conftest pins XDG_STATE_HOME at the temporary home.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from drive_support import Rig
from input_server import InputRecordingApp, attached, make_session
from stub_server import record_output
from test_theme_picker import (
    DRACULA,
    NORD_SURFACE,
    TERRACOTTA_SURFACE,
    _move,
    _notifications,
    _open_picker,
    _rect_fills,
    _user_dir,
)
from tsbc import _serve
from workspace_support import fixture_login, login

from tsb_tui import saved_theme
from tsb_tui.app import TsbTuiApp
from tsb_tui.config import load
from tsb_tui.screens.login import LoginScreen


def _state() -> Path:
    """The state file the pinned environment gives the app (XDG_STATE_HOME at the temporary home)."""
    return saved_theme.state_path()


# -- unit tests (fail today: no `saved_theme` module) --


def test_two_logins_round_trip(tmp_path: Path) -> None:
    # fails today: tsb_tui.saved_theme does not exist on main, so there is no round-trip to make.
    """TT16: the file is one table per login; a read of what `write` wrote must give every entry back
    exactly, or a login's saved look would be lost at the next start."""
    path = tmp_path / "theme.toml"
    entries = {_serve.USER: {"theme": "nord", "scheme": "dracula"},
               _serve.PEER: {"theme": "paper", "scheme": "solarized-dark"}}
    saved_theme.write(path, entries)
    assert saved_theme.read(path) == entries


def test_a_login_holding_quotes_dot_and_space_round_trips(tmp_path: Path) -> None:
    # fails today: no `saved_theme` module; a login name that is not a bare TOML key cannot round-trip.
    """The phase's Risk: a login name with `"`, `.` and a space is not a valid bare TOML key; every key
    is written quoted, so if quoting were dropped this login's entry would be unreadable at the next start."""
    path = tmp_path / "theme.toml"
    odd = 'he said "x" . z'
    entries = {odd: {"theme": "nord", "scheme": "dracula"}}
    saved_theme.write(path, entries)
    assert saved_theme.read(path) == entries


def test_the_file_is_0600_and_its_directory_0700(tmp_path: Path) -> None:
    # fails today: no `saved_theme` module, so no file and no modes to check.
    """TT11: the state file holds a person's look choices; it is written 0600 in a directory created
    0700, so a shared machine cannot read a login's entry."""
    path = tmp_path / "state" / "theme.toml"
    saved_theme.write(path, {_serve.USER: {"theme": "nord", "scheme": "dracula"}})
    assert path.stat().st_mode & 0o777 == 0o600, f"the file is {oct(path.stat().st_mode & 0o777)}"
    assert path.parent.stat().st_mode & 0o777 == 0o700, f"the directory is {oct(path.parent.stat().st_mode & 0o777)}"


def test_short_write_loops_until_all_bytes_are_written(tmp_path: Path, monkeypatch) -> None:
    # fails today: a single os.write call may write fewer bytes than requested; the old code does not loop.
    """os.write may write fewer bytes than requested. The write loop continues until all
    bytes are written, so a short write does not install a partial file. If the file were truncated,
    the next login would fail to parse it."""
    import tsb_tui.saved_theme as stm
    original_write = os.write
    write_count = [0]
    
    def short_write(fd, data):
        """Write at most 7 bytes per call."""
        write_count[0] += 1
        chunk = bytes(data[:7]) if len(data) > 7 else bytes(data)
        return original_write(fd, chunk)
    
    monkeypatch.setattr(stm.os, "write", short_write)
    
    path = tmp_path / "theme.toml"
    entries = {_serve.USER: {"theme": "nord", "scheme": "dracula"},
               _serve.PEER: {"theme": "paper", "scheme": "solarized-dark"}}
    saved_theme.write(path, entries)
    
    # The file was written in multiple calls
    assert write_count[0] > 1, f"only {write_count[0]} write call(s), expected multiple"
    # But the result is complete and parses to all entries
    assert saved_theme.read(path) == entries


def test_fsync_failure_leaves_old_file_unchanged(tmp_path: Path, monkeypatch) -> None:
    # fails today: an fsync error in the old code closes the fd but does not clean up the temp file.
    """When os.fsync raises, the error propagates, the old file is unchanged, and no temp
    file is left behind. If the temp file leaked, repeated failures would fill the directory."""
    path = tmp_path / "state" / "theme.toml"
    # Write an initial file
    saved_theme.write(path, {_serve.USER: {"theme": "terracotta", "scheme": "tsb-default"}})
    old_bytes = path.read_bytes()
    
    # Make fsync fail
    import tsb_tui.saved_theme as stm
    def failing_fsync(fd: int):
        raise OSError("fsync failed")
    
    monkeypatch.setattr(stm.os, "fsync", failing_fsync)
    
    # Try to write; it should fail
    try:
        saved_theme.write(path, {_serve.USER: {"theme": "nord", "scheme": "dracula"}})
    except OSError as exc:
        assert "fsync failed" in str(exc)
    else:
        raise AssertionError("write with failing fsync did not raise")
    
    # The old file is unchanged
    assert path.read_bytes() == old_bytes
    # No temp file leaked: the directory holds exactly the state file, nothing else
    leftover = sorted(p.name for p in path.parent.iterdir())
    assert leftover == ["theme.toml"], f"the directory holds more than the state file: {leftover}"


def test_two_writes_produce_distinct_temp_names(tmp_path: Path, monkeypatch) -> None:
    # fails today: the old code uses a fixed temp name, so two writers would collide.
    """mkstemp produces unique temp file names, so two TUIs saving at once use different
    files. If they shared one temp file, one writer's partial data could corrupt the other's."""
    path = tmp_path / "theme.toml"
    temp_names = []
    
    import tsb_tui.saved_theme as stm
    original_replace = os.replace
    
    def record_replace(src, dst):
        temp_names.append(Path(src).name)
        return original_replace(src, dst)
    
    monkeypatch.setattr(stm.os, "replace", record_replace)
    
    # Write twice
    saved_theme.write(path, {_serve.USER: {"theme": "nord", "scheme": "dracula"}})
    saved_theme.write(path, {_serve.USER: {"theme": "paper", "scheme": "solarized-dark"}})
    
    # The temp names are distinct
    assert len(temp_names) == 2
    assert temp_names[0] != temp_names[1], f"temp names are the same: {temp_names}"


def test_a_file_that_does_not_parse_raises_naming_it(tmp_path: Path) -> None:
    # fails today: no `saved_theme` module; nothing reads the file, so a broken one raises nothing.
    """TT17: a state file that cannot be parsed must raise naming the file and the field, so the operator
    can fix the right file, not guess which one is broken."""
    path = tmp_path / "theme.toml"
    path.write_text("[[[]\n", encoding="utf-8")
    try:
        saved_theme.read(path)
    except saved_theme.SavedThemeError as exc:
        assert str(path) in str(exc), f"the message does not name the file: {exc}"
    else:
        raise AssertionError("a file that does not parse did not raise")


def test_save_on_an_unparseable_file_raises_and_leaves_its_bytes(tmp_path: Path) -> None:
    # fails today: no `saved_theme` module, so there is no save to refuse.
    """TT17: `save` reads first; a file it cannot read must raise and its bytes stay untouched - overwriting
    what it could not read would destroy the other logins' entries."""
    path = tmp_path / "theme.toml"
    broken = "[[[]\n"
    path.write_text(broken, encoding="utf-8")
    try:
        saved_theme.save(path, _serve.USER, "nord", "dracula")
    except saved_theme.SavedThemeError as exc:
        assert str(path) in str(exc), f"the message does not name the file: {exc}"
    else:
        raise AssertionError("save on an unparseable file did not raise")
    assert path.read_text(encoding="utf-8") == broken, "save changed the bytes of a file it could not read"


def test_invalid_utf8_raises_naming_the_path(tmp_path: Path) -> None:
    # fails today: UnicodeDecodeError is not caught, so it escapes without naming the file.
    """A state file with invalid UTF-8 raises SavedThemeError naming the path, so the login
    toasts it clearly. If UnicodeDecodeError escaped uncaught, the login would crash instead of degrading."""
    path = tmp_path / "theme.toml"
    path.write_bytes(b"\xff\xfe invalid UTF-8")
    try:
        saved_theme.read(path)
    except saved_theme.SavedThemeError as exc:
        assert str(path) in str(exc), f"the message does not name the file: {exc}"
    else:
        raise AssertionError("invalid UTF-8 did not raise SavedThemeError")


def test_login_toasts_invalid_utf8_and_does_not_raise(serve, wait_until) -> None:
    # fails today: UnicodeDecodeError escapes uncaught, login crashes.
    """A state file of invalid UTF-8 bytes causes login to toast "cannot read saved theme"
    and continue, not raise. Save then refuses with its existing toast. If login raised, the user could
    not log in until they manually fixed or removed the file."""
    _state().parent.mkdir(parents=True, exist_ok=True)
    _state().write_bytes(b"\xff\xfe invalid UTF-8")

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, target="normal")
        record_output(serve_app, sid, b"ops@infra-db-02:~$ df -h /var\r\n")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await fixture_login(app, pilot, _serve.USER)
            # Login succeeded and toasted the error - the notification contains "could not parse"
            notifications = _notifications(app)
            assert any("could not parse" in n for n in notifications), \
                f"login did not toast the error: {notifications}"
            # Save refuses with its existing toast
            from tsb_tui.screens.theme_picker import ThemePicker
            await _open_picker(pilot, app, screen, wait_until)
            picker = app.screen
            assert isinstance(picker, ThemePicker)
            await pilot.press("s")
            await pilot.pause(0.1)
            notifications = _notifications(app)
            assert any("cannot save" in n and "could not be read" in n for n in notifications), \
                f"Save did not refuse: {notifications}"

    asyncio.run(check())


# -- pilot tests (login through fixture_login; fail today: nothing reads the state file) --


async def _login(serve, wait_until, cfg=None):
    """A login through the form, one painted session attached: the state Save and the saved look work over."""
    serve_app, url = serve(InputRecordingApp())
    sid = make_session(url, target="normal")
    record_output(serve_app, sid, b"ops@infra-db-02:~$ df -h /var\r\n")
    app = TsbTuiApp(url) if cfg is None else TsbTuiApp(url, cfg=cfg)
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await fixture_login(app, pilot)
        screen = await attached(pilot, app, sid, wait_until)
        yield app, screen, pilot


def test_the_saved_look_is_applied_at_login(serve, wait_until) -> None:
    # fails today: nothing reads the state file, so login opens on terracotta and tsb-default.
    """TT16: an entry for this login is applied right after login - the rail in Nord's surface, the canvas
    in Dracula's background, both names' source `saved for <user>` - so the next start opens with the look
    this person chose, not the packaged default."""
    saved_theme.write(_state(), {_serve.USER: {"theme": "nord", "scheme": "dracula"}})

    async def check() -> None:
        gen = _login(serve, wait_until)
        app, _, _ = await gen.__anext__()
        assert app.cfg["theme.name"] == "nord", f"the saved theme was not applied: {app.cfg['theme.name']!r}"
        assert app.cfg["theme.term.scheme"] == "dracula", f"the saved scheme was not applied: {app.cfg['theme.term.scheme']!r}"
        assert app.cfg.source("theme.name") == f"saved for {_serve.USER}", f"the theme's source: {app.cfg.source('theme.name')!r}"
        assert app.cfg.source("theme.term.scheme") == f"saved for {_serve.USER}", f"the scheme's source: {app.cfg.source('theme.term.scheme')!r}"
        fills = _rect_fills(app.export_screenshot())
        assert any(f == NORD_SURFACE and x < 4 for f, x in fills), "the rail is not in Nord's surface"
        assert any(f == DRACULA.background and x >= 4 for f, x in fills), "the canvas is not in Dracula's background"
        await gen.aclose()

    asyncio.run(check())


def test_a_higher_layer_wins_over_the_saved_choice(serve, wait_until) -> None:
    # fails today: nothing reads the state file, so there is no saved choice for a layer to beat.
    """TT12: the saved choice sits below the config file, the environment and --set; with `theme.name = "paper"`
    in the config file, the theme stays paper and only the scheme is taken from the entry. If the saved choice
    beat the config file, an operator's config would be silently overwritten at every login."""
    _user_dir().mkdir(parents=True, exist_ok=True)
    (_user_dir() / "config.toml").write_text('[theme]\nname = "paper"\n', encoding="utf-8")
    saved_theme.write(_state(), {_serve.USER: {"theme": "nord", "scheme": "dracula"}})

    async def check() -> None:
        gen = _login(serve, wait_until)
        app, _, _ = await gen.__anext__()
        assert app.cfg["theme.name"] == "paper", f"the config file's theme did not win: {app.cfg['theme.name']!r}"
        assert app.cfg.source("theme.name").startswith("config file"), f"the theme's source: {app.cfg.source('theme.name')!r}"
        assert app.cfg["theme.term.scheme"] == "dracula", f"the saved scheme was not applied: {app.cfg['theme.term.scheme']!r}"
        assert app.cfg.source("theme.term.scheme") == f"saved for {_serve.USER}", f"the scheme's source: {app.cfg.source('theme.term.scheme')!r}"
        fills = _rect_fills(app.export_screenshot())
        assert any(f == DRACULA.background and x >= 4 for f, x in fills), "the canvas is not in Dracula's background"
        await gen.aclose()

    asyncio.run(check())


def test_save_writes_only_this_login_s_entry(serve, wait_until) -> None:
    # fails today: there is no Save, so nothing writes the state file.
    """TT16: the file is one table per login; Save must change only this login's entry. If Save rewrote
    every entry, one person's default would silently change another login's next start."""
    saved_theme.write(_state(), {_serve.USER: {"theme": "terracotta", "scheme": "tsb-default"},
                                 _serve.PEER: {"theme": "nord", "scheme": "dracula"}})
    peer_before = re.search(r'\["fixture-peer"\][^[]*', _state().read_text(encoding="utf-8")).group(0)

    async def check() -> None:
        gen = _login(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        _move(picker, "themes", "paper")
        _move(picker, "schemes", "solarized-dark")
        await pilot.pause(0.2)
        await pilot.press("s")
        await wait_until(pilot, lambda: app.screen is screen and not screen.dialogs, "the picker to close on Save")
        text = _state().read_text(encoding="utf-8")
        peer_after = re.search(r'\["fixture-peer"\][^[]*', text).group(0)
        assert peer_after == peer_before, f"Save changed the other login's entry: {peer_after!r} vs {peer_before!r}"
        entries = saved_theme.read(_state())
        assert entries[_serve.USER] == {"theme": "paper", "scheme": "solarized-dark"}, \
            f"this login's entry is not the previewed pair: {entries[_serve.USER]!r}"
        assert any(f"Saved for {_serve.USER}" in m for m in _notifications(app)), "Save did not toast the login"
        await gen.aclose()

    asyncio.run(check())


def test_save_under_set_theme_name_writes_and_names_the_layer(serve, wait_until) -> None:
    # fails today: there is no Save, so nothing writes the file and no toast names a layer.
    """TT18: under `--set theme.name=…`, Save still writes this login's entry, and its toast says the higher
    layer wins at the next start. If the toast stayed silent, the operator would believe their saved theme
    takes effect at the next start when --set beats it."""
    cfg = load(overrides=["theme.name=nord"])
    assert cfg.source("theme.name") == "--set", f"the --set layer is not in force: {cfg.source('theme.name')!r}"

    async def check() -> None:
        gen = _login(serve, wait_until, cfg)
        app, screen, pilot = await gen.__anext__()
        picker = await _open_picker(pilot, app, screen, wait_until)
        _move(picker, "schemes", "dracula")
        await pilot.pause(0.2)
        await pilot.press("s")
        await wait_until(pilot, lambda: app.screen is screen and not screen.dialogs, "the picker to close on Save")
        entries = saved_theme.read(_state())
        assert entries[_serve.USER]["scheme"] == "dracula", f"Save did not write the entry: {entries.get(_serve.USER)!r}"
        assert any(f"Saved for {_serve.USER}" in m and "--set" in m for m in _notifications(app)), \
            f"the toast does not name --set: {_notifications(app)}"
        await gen.aclose()

    asyncio.run(check())


def test_a_bad_entry_toasts_the_file_and_the_field_and_stays_in_the_file(serve, wait_until) -> None:
    # fails today: nothing reads the state file, so a bad entry is never seen.
    """TT17: an entry naming a theme that cannot load is a toast naming the state file and `["<user>"].theme`;
    the look in force stays (terracotta) and the entry is kept, so the operator can fix the name in the file
    they are told about. If the entry were dropped, the person's saved choice vanishes with no message."""
    saved_theme.write(_state(), {_serve.USER: {"theme": "nosuch", "scheme": "tsb-default"}})

    async def check() -> None:
        gen = _login(serve, wait_until)
        app, _, _ = await gen.__anext__()
        toasts = _notifications(app)
        assert any(str(_state()) in m and f"{_serve.USER}" in m and "theme" in m for m in toasts), \
            f"no toast naming the state file and the field: {toasts}"
        assert app.cfg["theme.name"] == "terracotta", f"the look in force did not stay: {app.cfg['theme.name']!r}"
        fills = _rect_fills(app.export_screenshot())
        assert any(f == TERRACOTTA_SURFACE and x < 4 for f, x in fills), "the rail is not terracotta's surface"
        assert saved_theme.read(_state())[_serve.USER]["theme"] == "nosuch", "the bad entry was dropped from the file"
        await gen.aclose()

    asyncio.run(check())


def test_an_unreadable_file_toasts_at_login_and_save_is_refused(serve, wait_until) -> None:
    # fails today: nothing reads the state file, so an unreadable file is never noticed.
    """TT17: a state file that does not parse is toasted at login naming it, and Save refuses until it is
    fixed: writing over a file it cannot read would destroy every login's entry, so the file's bytes must
    stay exactly as they are."""
    path = _state()
    path.parent.mkdir(parents=True, exist_ok=True)
    broken = "[[[]\n"
    path.write_text(broken, encoding="utf-8")

    async def check() -> None:
        gen = _login(serve, wait_until)
        app, screen, pilot = await gen.__anext__()
        assert any(str(path) in m for m in _notifications(app)), "login did not toast the unreadable file"
        await _open_picker(pilot, app, screen, wait_until)
        await pilot.press("s")
        await pilot.pause(0.2)
        toasts = _notifications(app)
        assert any("cannot save" in m and str(path) in m for m in toasts), \
            f"Save was not refused with a toast: {toasts}"
        assert path.read_text(encoding="utf-8") == broken, "the refused Save changed the file's bytes"
        await gen.aclose()

    asyncio.run(check())


def test_two_logins_keep_their_own(serve, wait_until) -> None:
    # fails today: unsave_names resets only the names, leaving chrome, scheme and role values from the previous login.
    """TT16: each login gets its own entry; a login without an entry after one with it gets the default
    names back. The chrome, scheme and painted colors also match the fallback look, not just the two
    names. If the saved choice leaked across logins, the second person on a shared machine would start
    in the first person's look."""
    saved_theme.write(_state(), {_serve.USER: {"theme": "nord", "scheme": "dracula"},
                                 _serve.PEER: {"theme": "paper", "scheme": "solarized-dark"}})

    async def check() -> None:
        serve_app, url = serve(InputRecordingApp())
        sid = make_session(url, target="normal")
        record_output(serve_app, sid, b"ops@infra-db-02:~$ df -h /var\r\n")
        app = TsbTuiApp(url)
        async with app.run_test(size=(120, 40)) as pilot:
            await fixture_login(app, pilot, _serve.USER)
            assert app.cfg["theme.name"] == "nord" and app.cfg["theme.term.scheme"] == "dracula", \
                "the first login did not get its own look"
            # the fixture server allows PEER with the same password, so the second login goes through
            # `_to_login` in this same run (app.py:471), as a later login does for a person.
            app._to_login()
            await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen) and app.screen.query("#username"),
                             "the login form to mount")
            await login(app, pilot, _serve.PEER, _serve.PASSWORD)
            assert app.cfg["theme.name"] == "paper" and app.cfg["theme.term.scheme"] == "solarized-dark", \
                "the second login did not get its own look"
            assert app.cfg.source("theme.name") == f"saved for {_serve.PEER}", \
                f"the second login's source is not its own: {app.cfg.source('theme.name')!r}"
            # a login without an entry, after one with it: the names go back through unsave_names.
            # chrome, scheme and painted colors also match the default look.
            app._to_login()
            await wait_until(pilot, lambda: isinstance(app.screen, LoginScreen) and app.screen.query("#username"),
                             "the login form to mount")
            await login(app, pilot, _serve.VIEWER, _serve.PASSWORD)
            assert app.cfg["theme.name"] == "terracotta" and app.cfg["theme.term.scheme"] == "tsb-default", \
                f"a login without an entry did not get the defaults back: {app.cfg['theme.name']!r}/" \
                f"{app.cfg['theme.term.scheme']!r}"
            assert app.cfg.source("theme.name") == "default" and app.cfg.source("theme.term.scheme") == "default", \
                f"the restored names' sources: {app.cfg.source('theme.name')!r}/{app.cfg.source('theme.term.scheme')!r}"
            # chrome.id, scheme.slug and painted colors all match the default look
            assert app.cfg.chrome.id == "terracotta", \
                f"chrome.id is still {app.cfg.chrome.id!r}, not terracotta"
            assert app.cfg.scheme.slug == "tsb-default", \
                f"scheme.slug is still {app.cfg.scheme.slug!r}, not tsb-default"
            # Check a chrome role (surface) and the canvas background match the default look
            from tsb_tui.theme_files import packaged_dir, parse_scheme, parse_theme
            terracotta = parse_theme(packaged_dir("themes") / "terracotta.toml")
            tsb_default = parse_scheme(packaged_dir("schemes") / "tsb-default.json")
            assert app.cfg["theme.surface"] == terracotta.roles["surface"], \
                f"theme.surface is {app.cfg['theme.surface']!r}, not terracotta's"
            fills = _rect_fills(app.export_screenshot())
            assert any(f == tsb_default.background and x >= 4 for f, x in fills), \
                "the canvas background is not tsb-default's, it kept the previous login's scheme"

    asyncio.run(check())


def test_drive_ignores_the_saved_choice(serve, tmp_path) -> None:
    # fails today: there is no state file to read, so drive's look is the same as a person's.
    """TT19: drive never reads or writes the state file; with an entry present for the user, drive opens on
    terracotta/tsb-default and the file's bytes and mtime are unchanged at the end. If drive read it, an
    agent's `--set` look would be replaced by a human's saved default."""
    saved_theme.write(_state(), {_serve.USER: {"theme": "nord", "scheme": "dracula"}})
    before = _state().read_bytes()
    mtime = _state().stat().st_mtime_ns

    async def check() -> None:
        _serve_app, url = serve(InputRecordingApp())
        async with Rig(url, tmp_path=tmp_path, size=(120, 40)) as rig:
            await rig.login(_serve.USER)
            assert rig.app.cfg["theme.name"] == "terracotta", \
                f"drive applied the saved theme: {rig.app.cfg['theme.name']!r}"
            assert rig.app.cfg["theme.term.scheme"] == "tsb-default", \
                f"drive applied the saved scheme: {rig.app.cfg['theme.term.scheme']!r}"

    asyncio.run(check())
    assert _state().read_bytes() == before, "drive changed the state file's bytes"
    assert _state().stat().st_mtime_ns == mtime, "drive touched the state file"
